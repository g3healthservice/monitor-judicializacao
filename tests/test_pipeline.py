"""Pipeline end-to-end com DataJud mockado (httpx.MockTransport)."""
import json

import httpx
import pytest
from sqlmodel import select

from src.config.settings import Municipio, Settings
from src.ingest.client import DataJudClient
from src.pipeline import ingerir_tribunal
from src.store.db import get_session, init_db
from src.store.models import Alerta, Movimentacao, Processo
from tests.fixtures import resposta_datajud

MUNICIPIOS = [
    Municipio(nome="Sao Paulo", codigo_ibge="3550308", uf="SP", tribunal="tjsp",
              comarcas=["Sao Paulo"]),
    Municipio(nome="Campinas", codigo_ibge="3509502", uf="SP", tribunal="tjsp",
              comarcas=["Campinas"]),
]


def _mock_client(chamadas):
    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(json.loads(request.content))
        return httpx.Response(200, json=resposta_datajud())

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path/'test.db'}"
    init_db(url)
    return url


async def _rodar(db_url, settings):
    chamadas = []
    async with _mock_client(chamadas) as http:
        client = DataJudClient("tjsp", settings=settings, client=http)
        contadores = await ingerir_tribunal(
            "tjsp", MUNICIPIOS, settings=settings, client=client, database_url=db_url
        )
    return contadores, chamadas


@pytest.mark.asyncio
async def test_pipeline_end_to_end(db_url):
    settings = Settings(DATAJUD_API_KEY="test", DATABASE_URL=db_url, SALARIO_MINIMO=1518.00)
    contadores, chamadas = await _rodar(db_url, settings)

    assert contadores["ingeridos"] == 4
    assert contadores["novos"] == 4
    # Alertas: 3 processos enquadraveis (estadual, federal, oncologico); o LOCAL nao alerta.
    assert contadores["alertados"] == 3
    # PII detectada e descartada em todos os hits.
    assert contadores["pii_flags"] == 4

    # A query enviada filtrou por assunto de saude.
    filtros = chamadas[0]["query"]["bool"]["filter"]
    assert any("assuntos.codigo" in f.get("terms", {}) for f in filtros)

    with get_session(db_url) as s:
        procs = s.exec(select(Processo)).all()
        assert len(procs) == 4

        # Nenhuma coluna do modelo carrega PII (garantido por sanitize + whitelist).
        for p in procs:
            blob = json.dumps(p.model_dump(), default=str).lower()
            assert "fulano" not in blob
            assert "123.456.789" not in blob

        # Oncologico corretamente a 80%.
        onc = s.get(Processo, "0004000-00.2025.8.26.0100")
        assert onc.oncologico is True
        assert onc.percentual_ressarcivel == 0.80
        assert onc.valor_ressarcivel_estimado == 40000.0

        # Federal a 100%.
        fed = s.get(Processo, "0003000-00.2025.8.26.0114")
        assert fed.faixa == "FEDERAL_100"
        assert fed.municipio_ibge == "3509502"

        movs = s.exec(select(Movimentacao)).all()
        assert len(movs) == 4  # 1 movimento por processo


@pytest.mark.asyncio
async def test_pipeline_idempotente(db_url):
    settings = Settings(DATAJUD_API_KEY="test", DATABASE_URL=db_url, SALARIO_MINIMO=1518.00)
    await _rodar(db_url, settings)
    contadores2, _ = await _rodar(db_url, settings)

    # Reexecucao nao cria processos novos nem alertas novos.
    assert contadores2["novos"] == 0
    assert contadores2["alertados"] == 0

    with get_session(db_url) as s:
        assert len(s.exec(select(Processo)).all()) == 4
        assert len(s.exec(select(Alerta)).all()) == 3  # dedup por processo
        assert len(s.exec(select(Movimentacao)).all()) == 4  # movimentacoes nao duplicam

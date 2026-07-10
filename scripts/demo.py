"""Demo sem chave de API: roda o pipeline com resposta simulada do DataJud,
persiste em banco temporario e gera relatorio real do municipio piloto (Sao Paulo).

Uso: ./.venv/bin/python -m scripts.demo
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.config.logging_setup import setup_logging
from src.config.settings import Municipio, Settings
from src.ingest.client import DataJudClient
from src.pipeline import ingerir_tribunal
from src.report.aggregate import agregar_municipio
from src.report.dashboard import build_dataset, gerar_dashboard
from src.report.pdf import gerar_pdf
from src.report.xlsx import gerar_xlsx
from src.store.db import get_session, init_db

# Reaproveita a fixture de teste como "resposta real" do DataJud.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import resposta_datajud  # noqa: E402

MUNICIPIOS = [
    Municipio(nome="Sao Paulo", codigo_ibge="3550308", uf="SP", tribunal="tjsp",
              comarcas=["Sao Paulo"]),
    Municipio(nome="Campinas", codigo_ibge="3509502", uf="SP", tribunal="tjsp",
              comarcas=["Campinas"]),
    Municipio(nome="Guarulhos", codigo_ibge="3518800", uf="SP", tribunal="tjsp",
              comarcas=["Guarulhos"]),
]


def _mock_client():
    def handler(request):
        return httpx.Response(200, json=resposta_datajud())
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def main():
    setup_logging("INFO")
    out = Path(__file__).resolve().parents[1] / "out"
    db_url = f"sqlite:///{out/'demo.db'}"
    out.mkdir(exist_ok=True)
    (out / "demo.db").unlink(missing_ok=True)
    init_db(db_url)

    settings = Settings(DATAJUD_API_KEY="demo", DATABASE_URL=db_url, SALARIO_MINIMO=1518.00)

    print("\n=== 1) INGESTAO modo Brasil inteiro (TJ inteiro, municipio via IBGE) ===")
    async with _mock_client() as http:
        client = DataJudClient("tjsp", settings=settings, client=http)
        contadores = await ingerir_tribunal(
            "tjsp", None, settings=settings, client=client, database_url=db_url
        )
    print("Contadores:", json.dumps(contadores, ensure_ascii=False))

    print("\n=== 2) PROCESSOS CLASSIFICADOS (Sao Paulo, IBGE 3550308) ===")
    with get_session(db_url) as s:
        metricas = agregar_municipio(s, "3550308")
        print(f"{'Processo':<26} {'Faixa':<22} {'%':>5} {'Custo':>14} {'Ressarcivel':>16}")
        print("-" * 88)
        for p in metricas["processos"]:
            print(f"{p.numero_processo:<26} {p.faixa:<22} "
                  f"{(p.percentual_ressarcivel or 0):>4.0%} "
                  f"{(p.custo_anual_estimado or 0):>13,.0f} "
                  f"{(p.valor_ressarcivel_estimado or 0):>15,.2f}")
        print("-" * 88)
        print(f"n_processos={metricas['n_processos']}  "
              f"dinheiro_na_mesa=R$ {metricas['valor_total_ressarcivel']:,.2f}  "
              f"faixas={metricas['distribuicao_faixa']}")

        print("\n=== 3) RELATORIOS ===")
        xlsx = gerar_xlsx(metricas, out / "relatorio_sao_paulo.xlsx", municipio_nome="Sao Paulo")
        pdf = gerar_pdf(metricas, out / "relatorio_sao_paulo.pdf", municipio_nome="Sao Paulo")
        print(f"XLSX: {xlsx}")
        print(f"PDF:  {pdf}")

        print("\n=== 4) DASHBOARD HTML (online, padrao Raio-X) ===")
        payload = build_dataset(s, None)  # agrupa por todos os municipios do banco
        dash = gerar_dashboard(payload, out / "dashboard.html")
        print(f"Dashboard: {dash}")
        print(f"  {payload['totais']['n_municipios']} municipios · "
              f"dinheiro na mesa R$ {payload['totais']['valor_total_ressarcivel']:,.2f}")


if __name__ == "__main__":
    asyncio.run(main())

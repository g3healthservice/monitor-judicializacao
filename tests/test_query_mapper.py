"""Query builder e mapper (heuristicas de CID/oncologico e municipio)."""
from src.config.settings import Municipio
from src.ingest.mapper import (
    detectar_cid,
    is_oncologico,
    map_source_to_processo_fields,
    resolver_municipio,
)
from src.ingest.query import build_query, endpoint_tribunal
from src.privacy.sanitize import sanitize
from tests.fixtures import resposta_datajud

MUNICIPIOS = [
    Municipio(nome="Sao Paulo", codigo_ibge="3550308", uf="SP", tribunal="tjsp",
              comarcas=["Sao Paulo"]),
    Municipio(nome="Campinas", codigo_ibge="3509502", uf="SP", tribunal="tjsp",
              comarcas=["Campinas"]),
]


def test_endpoint():
    assert endpoint_tribunal("TJSP").endswith("/api_publica_tjsp/_search")


def test_build_query_filtra_assuntos_e_ibge():
    q = build_query(codigos_ibge=["3550308"], data_ajuizamento_gte="2025-01-01",
                    page_size=50, search_after=[1, "x"])
    filtros = q["query"]["bool"]["filter"]
    assert any("assuntos.codigo" in f.get("terms", {}) for f in filtros)
    assert any("orgaoJulgador.codigoMunicipioIBGE" in f.get("terms", {}) for f in filtros)
    assert q["size"] == 50
    assert q["search_after"] == [1, "x"]
    assert q["sort"][0]["dataAjuizamento"]["order"] == "desc"  # mais recentes primeiro


def test_build_query_data_compacta():
    # Data ISO deve virar formato compacto (senao o filtro do DataJud nao casa).
    q = build_query(data_ajuizamento_gte="2026-01-01")
    filtros = q["query"]["bool"]["filter"]
    rng = next(f for f in filtros if "range" in f)
    assert rng["range"]["dataAjuizamento"]["gte"] == "20260101"


def test_categoria_de_assuntos():
    from src.config.constants import categoria_de_assuntos
    assert categoria_de_assuntos(["Fornecimento de medicamentos"]) == "MEDICAMENTO"
    assert categoria_de_assuntos(["Oncológico"]) == "ONCOLOGICO"
    assert categoria_de_assuntos(["Não padronizado"]) == "MEDICAMENTO_NAO_PADRONIZADO"
    assert categoria_de_assuntos(["Fornecimento de insumos"]) == "INSUMO"
    assert categoria_de_assuntos(["Plano de Saúde"]) == "PLANO_SAUDE"
    assert categoria_de_assuntos([]) == "OUTROS"


def test_detectar_cid():
    assert detectar_cid("Tratamento Oncologico (CID C50)") == "C50"
    assert detectar_cid("sem cid aqui") is None


def test_is_oncologico_por_cid_e_texto():
    assert is_oncologico("C50", "") is True
    assert is_oncologico(None, "pedido de quimioterapia") is True
    assert is_oncologico(None, "fornecimento de insulina") is False


def test_resolver_municipio():
    source = sanitize(resposta_datajud()["hits"]["hits"][2]["_source"])  # ibge Campinas
    m = resolver_municipio(source, MUNICIPIOS)
    assert m is not None and m.nome == "Campinas"


def test_map_source_oncologico():
    source = sanitize(resposta_datajud()["hits"]["hits"][3]["_source"])
    campos = map_source_to_processo_fields(source, "tjsp", municipio=MUNICIPIOS[0])
    assert campos["oncologico"] is True
    assert campos["cid"] == "C50"
    assert campos["municipio_ibge"] == "3550308"
    assert campos["origem_estimativa"] == "valor_causa"
    assert campos["custo_anual_estimado"] == 50000.0

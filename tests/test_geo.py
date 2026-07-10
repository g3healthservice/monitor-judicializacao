"""Modo Brasil inteiro: resolucao de municipio pela tabela IBGE."""
from src.config.geo import load_ibge_map, municipio_from_orgao, resolve_ibge
from src.config.settings import load_tribunais


def test_ibge_map_carrega():
    m = load_ibge_map()
    assert len(m) > 5000
    assert m["3550308"][0].startswith("S")  # Sao Paulo
    assert m["3550308"][1] == "SP"


def test_resolve_ibge_7digitos():
    mun = resolve_ibge(3550308, "tjsp")
    assert mun is not None
    assert mun.uf == "SP"
    assert mun.codigo_ibge == "3550308"


def test_resolve_ibge_6digitos():
    # 355030 (sem DV) deve casar com 3550308
    mun = resolve_ibge("355030", "tjsp")
    assert mun is not None
    assert mun.uf == "SP"


def test_resolve_ibge_desconhecido():
    assert resolve_ibge("9999999", "tjsp") is None
    assert resolve_ibge(None, "tjsp") is None


def test_municipio_from_orgao():
    # Nome do orgao carrega o municipio (DataJud publico omite o IBGE).
    assert municipio_from_orgao("01 FAZENDA PUBLICA DE BAURU", "SP", "tjsp").nome == "Bauru"
    assert municipio_from_orgao("02 FAZENDA PUBLICA DE CAMPINAS", "SP", "tjsp").codigo_ibge == "3509502"
    # Foro CENTRAL designa a capital.
    assert municipio_from_orgao("SETOR ... FAZENDA PUBLICA DE CENTRAL", "SP", "tjsp").codigo_ibge == "3550308"
    # 'D Oeste' nao pode quebrar o nome.
    assert municipio_from_orgao("02 CIVEL DE SANTA BARBARA D OESTE", "SP", "tjsp").nome == "Santa Bárbara d'Oeste"
    # Camara de 2a instancia nao tem municipio.
    assert municipio_from_orgao("13 CAMARA DE DIREITO PUBLICO", "SP", "tjsp") is None


def test_tribunais_27_tjs():
    tribunais = load_tribunais()
    assert len(tribunais) == 27
    siglas = {t.sigla for t in tribunais}
    assert "tjsp" in siglas and "tjrj" in siglas and "tjdft" in siglas
    assert len({t.uf for t in tribunais}) == 27

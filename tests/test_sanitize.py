"""LGPD: sanitize() nao pode deixar passar PII."""
from src.privacy.sanitize import detectar_pii, sanitize
from tests.fixtures import resposta_datajud


def test_sanitize_remove_pii():
    source = resposta_datajud()["hits"]["hits"][0]["_source"]
    limpo = sanitize(source)

    # Campos PII descartados.
    assert "nomeParte" not in limpo
    assert "cpfParte" not in limpo
    assert "poloAtivo" not in limpo

    # Metadados publicos preservados.
    assert limpo["numeroProcesso"] == "0001000-00.2025.8.26.0100"
    assert limpo["assuntos"][0]["codigo"] == 12481
    assert limpo["orgaoJulgador"]["codigoMunicipioIBGE"] == 3550308


def test_sanitize_preserva_nome_de_classe_e_assunto():
    source = resposta_datajud()["hits"]["hits"][0]["_source"]
    limpo = sanitize(source)
    # 'nome' de classe/assunto/movimento e legitimo (nao e pessoa).
    assert limpo["classe"]["nome"] == "Procedimento Comum Civel"
    assert "nome" in limpo["assuntos"][0]
    assert "nome" in limpo["movimentos"][0]


def test_detectar_pii_flag():
    source = resposta_datajud()["hits"]["hits"][0]["_source"]
    flags = detectar_pii(source)
    # Deve sinalizar os campos sensiveis do polo/parte/cpf.
    assert any("nomeParte" in f for f in flags)
    assert any("cpfParte" in f for f in flags)
    assert any("poloAtivo" in f for f in flags)


def test_detectar_pii_limpo_nao_flag():
    source = resposta_datajud()["hits"]["hits"][0]["_source"]
    limpo = sanitize(source)
    # Apos sanitize nao deve sobrar nada sinalizado.
    assert detectar_pii(limpo) == []

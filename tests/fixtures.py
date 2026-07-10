"""Fixtures: resposta simulada do DataJud (formato Elasticsearch).

Inclui deliberadamente campos com cara de PII (nomeParte, cpf) para provar que
o sanitize() os descarta. valorCausa e injetado para exercitar a classificacao
na v1 (em que a estimativa vem do valor da causa).
"""

# Salario minimo de referencia dos testes: 1518.00
#   7 SM  = 10.626,00  (limiar estadual)
#   210 SM = 318.780,00 (limiar federal)


def _hit(numero, ibge, assunto, valor_causa, dataAjuizamento, classe_nome, sort,
         movimento_nome="Distribuicao", extra_source=None):
    source = {
        "numeroProcesso": numero,
        "tribunal": "TJSP",
        "grau": "G1",
        "classe": {"codigo": 1, "nome": classe_nome},
        "assuntos": [{"codigo": assunto, "nome": "Fornecimento de Medicamentos"}],
        "orgaoJulgador": {"codigo": 100, "nome": "Vara da Fazenda Publica",
                          "codigoMunicipioIBGE": ibge},
        "dataAjuizamento": dataAjuizamento,
        "valorCausa": valor_causa,
        "movimentos": [{"codigo": 26, "nome": movimento_nome, "dataHora": dataAjuizamento}],
        # --- Campos que NAO devem ser persistidos (PII) ---
        "nomeParte": "Fulano de Tal da Silva",
        "cpfParte": "123.456.789-00",
        "poloAtivo": [{"nome": "Fulano de Tal", "documento": "12345678900"}],
    }
    if extra_source:
        source.update(extra_source)
    return {"_index": "api_publica_tjsp", "_source": source, "sort": sort}


def resposta_datajud():
    """Uma pagina com 4 processos cobrindo as 4 faixas + heuristica oncologico."""
    return {
        "hits": {
            "total": {"value": 4},
            "hits": [
                # LOCAL (< 7 SM): valor 5.000
                _hit("0001000-00.2025.8.26.0100", 3550308, 12481, 5000.00,
                     "2025-04-01", "Procedimento Comum Civel", [1, "0001000"]),
                # ESTADUAL (7..210 SM): valor 50.000
                _hit("0002000-00.2025.8.26.0100", 3550308, 12482 if False else 12481, 50000.00,
                     "2025-04-02", "Procedimento Comum Civel", [2, "0002000"]),
                # FEDERAL (>= 210 SM): valor 400.000
                _hit("0003000-00.2025.8.26.0114", 3509502, 12491, 400000.00,
                     "2025-04-03", "Procedimento Comum Civel", [3, "0003000"]),
                # ONCOLOGICO (heuristica pelo texto) com valor estadual: 50.000 -> 80%
                _hit("0004000-00.2025.8.26.0100", 3550308, 12495, 50000.00,
                     "2025-04-04", "Procedimento Comum - Tratamento Oncologico (CID C50)",
                     [4, "0004000"],
                     movimento_nome="Pedido de quimioterapia antineoplasica"),
            ],
        }
    }

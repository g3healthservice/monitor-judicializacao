from pathlib import Path

import pytest

from licitacoes.coleta import eh_medicamento, numero_pregao
from licitacoes.sinais import carregar_regras, frase_para_regex
from licitacoes.texto import normalizar, trecho

REGRAS = carregar_regras(Path(__file__).resolve().parents[1] / "licitacoes" / "sinais.yaml")

# Trecho real do Edital PE SRP 90114/2026 (contratacao 250005-709-2026), como o
# pypdf o extrai: com espacos quebrados no meio das palavras.
EDITAL_90114 = """
Qualificação Técnica:
9.27. No caso de empresa com medicamento sem registro na ANVISA, sujeito à importação, em caráter de excepcionalidade:
9.27.1. O produtos deve ser pré-qualificado pela Organização Mundial de Saúde (OMS), conforme o art. 4º, da Resolução RDC 203
/2017;
9.27.2.  Quando o tipo de produto ofertado não for o bjeto de programas de pré-qualificação da OMS, pode rá ser autorizada a
importação mediante:
9.27.2.1. Comprovante de registro válido em país cu ja autoridade regulatória competente seja membro do  Conselho Internacional
para Harmonização de Requisitos Técnicos de Produto s Farmacêuticos de Uso Humano ( International Council for Harmonisation
) - conforme art. 4º, §1 da RDC 203/2017; eof Technical Requirementsfor Pharmaceuticals for Human Use - ICH
3.1.2. Poderão participar empresas estrangeiras que possuam portaria de autorização.
8.1.13. Emitir Licença de Importação (LI) e respectivas alterações.
"""

OBJETO_709 = ("Pregão Eletrônico SRP nº 900114/2026 para fins de Registro de Preço para aquisição de "
              "Tolvaptana, Quinidina, Estiripentol, Epinefrina e Diazóxido, para atendimento às demandas "
              "judiciais da Coordenação-Geral de Execução e Logística de Demandas Judiciais em Saúde – CGLJUD")


def ids(texto):
    return {a.sinal_id for a in REGRAS.buscar(texto)}


def test_normalizar_preserva_comprimento():
    t = "Licença de Importação nº 203 – ÇÃO"
    assert len(normalizar(t)) == len(t)
    assert normalizar(t) == "licenca de importacao no 203 – cao"


def test_frase_tolera_espaco_quebrado():
    import re
    assert re.search(frase_para_regex("boas praticas"), normalizar("boas prátic as de fabricação"))


def test_edital_90114_marca_todos_os_sinais():
    achados = ids(EDITAL_90114 + OBJETO_709)
    assert achados == {"sem_registro_anvisa", "importacao_excepcional", "ich_ou_oms",
                       "empresa_estrangeira", "importacao_logistica", "demanda_judicial"}
    nota = REGRAS.pontuar(achados)
    assert nota["candidata"] and nota["tem_alto"]
    assert nota["pontuacao"] == 3 + 3 + 2 + 2 + 2 + 1


def test_trecho_e_a_frase_exata():
    a = {x.sinal_id: x for x in REGRAS.buscar(EDITAL_90114)}["sem_registro_anvisa"]
    assert "9.27. No caso de empresa com medicamento sem registro na ANVISA" in a.trecho


@pytest.mark.parametrize("texto", [
    "conforme RDC nº 203/2017", "Resolução RDC 488/2021", "RDC n. 203", "RDC Nº 488, de 2021",
])
def test_rdc(texto):
    assert "importacao_excepcional" in ids(texto)


@pytest.mark.parametrize("texto", ["RDC 2030", "RDC nº 768/2022", "RDC 47"])
def test_rdc_nao_confunde(texto):
    assert "importacao_excepcional" not in ids(texto)


def test_ich_exige_palavra_inteira():
    assert "ich_ou_oms" not in ids("ficha técnica de Michigan")
    assert "ich_ou_oms" in ids("país membro do ICH")


def test_edital_generico_nao_e_candidato():
    # Clausulas comuns a quase todo edital da DLOG: estrangeiras + importacao + judicial.
    generico = ("Poderão participar empresas estrangeiras. Emitir Licença de Importação. "
                "Atendimento a demandas judiciais.")
    nota = REGRAS.pontuar(ids(generico))
    assert nota["pontuacao"] == 5 and not nota["candidata"]


@pytest.mark.parametrize("texto,esperado", [
    (OBJETO_709, "90114/2026"),
    ("EDITAL PE SRP 90114_2026 Rep..pdf", "90114/2026"),
    ("PE 90072/2026 - AQUISIÇÃO DE MEDICAMENTOS", "90072/2026"),
    ("Aquisição de DOXICICLINA, 100MG", None),
])
def test_numero_pregao(texto, esperado):
    assert numero_pregao(texto) == esperado


def test_eh_medicamento():
    assert eh_medicamento([{"ncmNbsCodigo": "30049099"}], "")
    assert not eh_medicamento([{"ncmNbsCodigo": "87032100"}], "Aquisição de veículos")
    assert eh_medicamento([], "Aquisição de MEDICAMENTOS para demandas judiciais")


def test_trecho_compacta_espacos():
    t = "Intro qualquer.\n9.27. No caso   de empresa\ncom produto x. Depois vem outra."
    i = t.find("empresa")
    assert trecho(t, i, i + 7) == "9.27. No caso de empresa com produto x."

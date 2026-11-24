"""Etapa 3: custo desembarcado (mesma formula da planilha) e nota de viabilidade."""
from datetime import datetime
from pathlib import Path

import pytest

from licitacoes.viabilidade import Premissas, avaliar_item, carregar_cotacoes, carregar_premissas

RAIZ = Path(__file__).resolve().parents[1]
P = carregar_premissas(RAIZ / "licitacoes" / "premissas.yaml")
AGORA = datetime(2026, 11, 24, 12, 0)
EDITAL_SEM_REGISTRO = {"sem_registro_anvisa", "importacao_excepcional", "ich_ou_oms"}


@pytest.mark.parametrize("inr,esperado", [(125, 7.43), (250, 14.87), (8000, 475.73), (13000, 773.06), (19.8, 1.18)])
def test_custo_igual_a_planilha(inr, esperado):
    # Valores da coluna "Índia: R$/unid. desembarcado (est.)" da planilha de comparativo.
    assert round(P.custo(inr), 2) == esperado


def test_fob_maximo_inverte_o_custo():
    assert P.custo(P.fob_maximo_inr(6.34)) == pytest.approx(6.34)


def test_cenario_imposto_de_18():
    p = Premissas(imposto=0.18)
    assert p.custo(125) == pytest.approx(125 * 0.055 * 1.06 * 1.20)


def test_cotacoes_do_repositorio():
    cot = carregar_cotacoes(RAIZ / "cotacoes.csv")
    assert cot[("250005-709-2026", 1)]["preco_inr"] == 125
    assert cot[("250005-709-2026", 1)]["referencia_brl"] == 6.34
    assert cot[("250005-709-2026", 3)]["preco_inr"] is None


def item(**kw):
    base = {"numero_item": 1, "quantidade": 20340, "fase": "propostas_abertas",
            "data_encerramento": "2026-12-09T12:00:00", "valor_unitario_estimado": 0, "orcamento_sigiloso": 1}
    base.update(kw)
    return base


def test_nota_completa():
    cot = {"preco_inr": 100, "referencia_brl": 10.0, "fabricantes_ich_oms": 2, "capacidade_fornecedor": 40000}
    f = avaliar_item(item(), EDITAL_SEM_REGISTRO, cot, P, AGORA)
    assert f["cobertura"] == 100
    # custo 5,95 -> margem 40,5% -> 100; 2 fabricantes -> 80; 15 dias -> 100; escala 0,5x -> 100
    assert f["criterios"] == {"admissao": 1.0, "margem": 1.0, "fornecedor": 0.8, "prazo": pytest.approx(1.0),
                              "escala": 1.0}
    assert f["nota"] == 97


def test_sem_dado_fica_fora_e_baixa_a_cobertura():
    f = avaliar_item(item(), EDITAL_SEM_REGISTRO, {}, P, AGORA)
    assert f["criterios"]["margem"] is None and f["criterios"]["fornecedor"] is None
    assert f["cobertura"] == 45  # admissao 30 + prazo 15 de 100
    assert f["nota"] == 73       # 30 + 15 com dado, 55 de peso sem dado a 50%
    assert "sem preço indiano" in f["motivos"]["margem"]


def test_margem_negativa_e_prazo_vencido():
    cot = {"preco_inr": 125, "referencia_brl": 6.34, "prazo_manual": "2026-11-23T12:00"}
    f = avaliar_item(item(fase="em_julgamento"), EDITAL_SEM_REGISTRO, cot, P, AGORA)
    assert f["criterios"]["margem"] == 0.0 and f["criterios"]["prazo"] == 0.0
    assert "margem -17%" in f["motivos"]["margem"]
    assert "vencido" in f["motivos"]["prazo"]


def test_admissao_por_historico_e_escala_alta():
    cot = {"capacidade_fornecedor": 100000}
    f = avaliar_item(item(quantidade=865624), {"historico_fracasso"}, cot, P, AGORA)
    assert f["criterios"]["admissao"] == 0.4
    assert f["criterios"]["escala"] == 0.0  # 8,7x a capacidade

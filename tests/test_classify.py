"""Classificacao Tema 1.234: faixas, limiares e precedencia do oncologico."""
from src.classify.tema1234 import classificar
from src.config.constants import (
    FAIXA_ESTADUAL,
    FAIXA_FEDERAL,
    FAIXA_LOCAL,
    FAIXA_ONCOLOGICO,
)
from src.config.settings import Settings

SM = Settings(SALARIO_MINIMO=1518.00)
# limiares: estadual = 10.626,00 ; federal = 318.780,00


def test_faixa_local_sem_ressarcimento():
    r = classificar(5000.0, settings=SM)
    assert r.faixa == FAIXA_LOCAL
    assert r.percentual_ressarcivel == 0.0
    assert r.justica_competente == "LOCAL"
    assert r.valor_ressarcivel_estimado == 0.0


def test_faixa_estadual_65():
    r = classificar(50000.0, settings=SM)
    assert r.faixa == FAIXA_ESTADUAL
    assert r.percentual_ressarcivel == 0.65
    assert r.justica_competente == "ESTADUAL"
    assert r.valor_ressarcivel_estimado == 32500.0


def test_faixa_federal_100():
    r = classificar(400000.0, settings=SM)
    assert r.faixa == FAIXA_FEDERAL
    assert r.percentual_ressarcivel == 1.0
    assert r.justica_competente == "FEDERAL"
    assert r.valor_ressarcivel_estimado == 400000.0


def test_limiar_exato_estadual():
    r = classificar(SM.limiar_estadual_valor, settings=SM)
    assert r.faixa == FAIXA_ESTADUAL


def test_limiar_exato_federal():
    r = classificar(SM.limiar_federal_valor, settings=SM)
    assert r.faixa == FAIXA_FEDERAL


def test_oncologico_sobrepoe_estadual():
    # 50k cai na estadual (65%), mas oncologico eleva para 80%.
    r = classificar(50000.0, oncologico=True, settings=SM)
    assert r.faixa == FAIXA_ONCOLOGICO
    assert r.percentual_ressarcivel == 0.80
    assert r.valor_ressarcivel_estimado == 40000.0


def test_oncologico_nao_reduz_federal():
    # Federal ja e 100%; oncologico nao pode reduzir para 80%.
    r = classificar(400000.0, oncologico=True, settings=SM)
    assert r.faixa == FAIXA_FEDERAL
    assert r.percentual_ressarcivel == 1.0


def test_oncologico_abaixo_de_7sm_vira_ressarcivel():
    # Abaixo de 7 SM seria LOCAL; oncologico torna ressarcivel a 80%.
    r = classificar(5000.0, oncologico=True, settings=SM)
    assert r.faixa == FAIXA_ONCOLOGICO
    assert r.percentual_ressarcivel == 0.80
    assert r.justica_competente == "ESTADUAL"


def test_custo_indefinido():
    r = classificar(None, settings=SM)
    assert r.justica_competente == "INDEFINIDO"
    assert r.valor_ressarcivel_estimado is None


def test_limiares_derivam_do_salario_minimo():
    sm2 = Settings(SALARIO_MINIMO=2000.0)
    assert sm2.limiar_estadual_valor == 14000.0
    assert sm2.limiar_federal_valor == 420000.0

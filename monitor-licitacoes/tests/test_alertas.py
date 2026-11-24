"""Etapa 4: simulador de cenarios e alertas (sem rede nem SMTP de verdade)."""
from datetime import datetime
from pathlib import Path

import pytest

from licitacoes import alertas, db, simulador
from licitacoes.coleta import Coletor
from licitacoes.config import Config
from licitacoes.sinais import carregar_regras
from licitacoes.viabilidade import carregar_premissas

from .test_resultados import ClienteComResultado

RAIZ = Path(__file__).resolve().parents[1]
REGRAS = carregar_regras(RAIZ / "licitacoes" / "sinais.yaml")
PREM = RAIZ / "licitacoes" / "premissas.yaml"
AGORA = datetime(2026, 10, 1, 9, 0)  # PE 90114: propostas ate 15/10/2026


@pytest.fixture
def banco(tmp_path):
    cfg = Config(db_path=str(tmp_path / "m.db"))
    con = db.conectar(cfg.db_path)
    cli = ClienteComResultado()
    Coletor(cfg, REGRAS, con, cli).coletar()
    return cfg, con, cli


COT = {("250005-709-2026", 1): {"preco_inr": 125.0, "referencia_brl": 6.34, "referencia_tipo": "edital",
                                "medicamento": "Tolvaptana 15 mg", "prazo_manual": "2026-10-02T12:00",
                                "observacao": "diligencia"}}


def test_cenarios_e_acima_da_referencia(banco):
    _cfg, con, _cli = banco
    cen = simulador.carregar_cenarios(PREM, carregar_premissas(PREM))
    assert set(cen) == {"base", "cambio", "imposto_10", "imposto_18", "logistica", "volume"}
    sim = simulador.simular(con, cen, COT)
    assert len(sim) == 1
    por = {c["cenario"]: c for c in sim[0]["cenarios"]}
    assert round(por["base"]["custo"], 2) == 7.43
    assert round(por["imposto_18"]["custo"], 2) == round(125 * 0.055 * 1.06 * 1.20, 2)
    assert round(por["cambio"]["custo"], 2) == round(125 * 0.0605 * 1.06 * 1.02, 2)
    assert all(c["acima_da_referencia"] for c in sim[0]["cenarios"])
    assert "#b00020" in simulador.html(sim)
    # FOB maximo com desconto e o inverso exato do custo
    p = cen["volume"]["premissas"]
    assert p.custo(p.fob_maximo_inr(6.34)) == pytest.approx(6.34)


def test_alertas_sem_repeticao(banco):
    _cfg, con, cli = banco
    n = alertas.gerar(con, COT, 48, AGORA)
    tipos = sorted(a["tipo"] for a in alertas.pendentes(con, AGORA))
    # 90114 e candidata com propostas abertas; prazo informado do item 1 em 27 h.
    assert n == 2 and tipos == ["edital_novo", "prazo"]
    assert alertas.pendentes(con, AGORA)[0]["tipo"] == "prazo"  # prazos primeiro

    enviados = []
    ok = alertas.enviar(con, alertas.pendentes(con, AGORA), canais=["fake"])
    assert ok == ["fake"] and alertas.pendentes(con, AGORA) == []

    # Mesma situacao: nada novo.
    assert alertas.gerar(con, COT, 48, AGORA) == 0

    # O item 1 e homologado (evento de situacao) -> alerta de "outras mudancas".
    cli.situacao_709 = 2
    Coletor(Config(db_path=_cfg.db_path), REGRAS, con, cli).acompanhar()
    assert alertas.gerar(con, COT, 48, AGORA) == 3  # situacao, vencedor, ata
    assert {a["tipo"] for a in alertas.pendentes(con, AGORA)} == {"situacao"}


def test_item_fracassado_vira_sem_sucesso(banco):
    _cfg, con, cli = banco
    alertas.gerar(con, COT, 48, AGORA)
    cli.situacao_709 = 5
    Coletor(Config(db_path=_cfg.db_path), REGRAS, con, cli).acompanhar()
    alertas.gerar(con, COT, 48, AGORA)
    sem = [a for a in alertas.pendentes(con, AGORA) if a["tipo"] == "sem_sucesso"]
    assert len(sem) == 1 and "Em andamento → Fracassado" in sem[0]["detalhe"]
    assert "itens fracassados" in alertas.texto(sem).lower()


def test_prazo_vencido_e_descartado(banco):
    _cfg, con, _cli = banco
    alertas.gerar(con, COT, 48, AGORA)
    depois = datetime(2026, 10, 3, 9, 0)  # prazo de 02/10 12h ja passou
    assert [a["tipo"] for a in alertas.pendentes(con, depois)] == ["edital_novo"]


def test_sem_canal_fica_pendente(banco, monkeypatch):
    _cfg, con, _cli = banco
    for v in ("SMTP_HOST", "ALERT_EMAIL_TO", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(v, raising=False)
    alertas.gerar(con, COT, 48, AGORA)
    assert alertas.enviar(con, alertas.pendentes(con, AGORA)) == []
    assert len(alertas.pendentes(con, AGORA)) == 2


def test_email_monta_mensagem(banco, monkeypatch):
    _cfg, con, _cli = banco
    enviadas = []

    class SMTPFalso:
        def __init__(self, host, porta, timeout=None):
            self.host, self.porta = host, porta
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, s): self.login_ok = (u, s)
        def send_message(self, m): enviadas.append(m)

    monkeypatch.setattr(alertas.smtplib, "SMTP", SMTPFalso)
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo")
    monkeypatch.setenv("SMTP_USER", "robo@exemplo")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setenv("ALERT_EMAIL_TO", "a@exemplo, b@exemplo")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    alertas.gerar(con, COT, 48, AGORA)
    assert alertas.enviar(con, alertas.pendentes(con, AGORA)) == ["email"]
    m = enviadas[0]
    assert m["To"] == "a@exemplo, b@exemplo"
    assert "1 prazo(s) em 48 h" in m["Subject"] and "1 edital(is) novo(s)" in m["Subject"]
    corpo_html = m.get_body(("html",)).get_content()
    assert "Prazos a menos de 48 horas" in corpo_html and "PE 90114/2026" in corpo_html

"""Despacho de alertas: log estruturado + e-mail SMTP opcional; stubs Slack/WA.

Deduplicacao por dedup_key = numero_processo (um processo nunca alerta 2x).
"""
from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from typing import Optional

from ..config.logging_setup import get_logger
from ..config.settings import Settings, get_settings
from ..store import repository as repo
from ..store.models import Alerta, Processo
from sqlmodel import Session

log = get_logger("alert")


def _resumo(proc: Processo) -> dict:
    return {
        "numero_processo": proc.numero_processo,
        "tribunal": proc.tribunal,
        "municipio_ibge": proc.municipio_ibge,
        "uf": proc.uf,
        "comarca": proc.comarca,
        "faixa": proc.faixa,
        "justica_competente": proc.justica_competente,
        "percentual_ressarcivel": proc.percentual_ressarcivel,
        "custo_anual_estimado": proc.custo_anual_estimado,
        "valor_ressarcivel_estimado": proc.valor_ressarcivel_estimado,
        "cid": proc.cid,
        "oncologico": proc.oncologico,
    }


def despachar_alerta(
    session: Session, proc: Processo, settings: Optional[Settings] = None
) -> bool:
    """Registra e despacha alerta se inedito. Retorna True se despachou."""
    settings = settings or get_settings()
    dedup_key = proc.numero_processo
    payload = _resumo(proc)

    alerta = Alerta(
        numero_processo=proc.numero_processo,
        tipo="NOVA_ACAO_ENQUADRAVEL",
        canal="log",
        dedup_key=dedup_key,
        payload=json.dumps(payload, ensure_ascii=False),
    )
    registrado = repo.registrar_alerta(session, alerta)
    if registrado is None:
        return False  # ja alertado antes

    # Canal 1: log estruturado (sempre).
    log.info("ALERTA_NOVA_ACAO", extra={"contexto": payload})

    # Canal 2: e-mail (opcional).
    if settings.alert_email_enabled:
        try:
            _enviar_email(settings, payload)
        except Exception as exc:  # nao derruba o pipeline por falha de e-mail
            log.warning("falha_envio_email", extra={"contexto": {"erro": str(exc)}})

    # Canais 3/4: Slack / WhatsApp (stubs).
    _stub_slack(payload)
    _stub_whatsapp(payload)

    return True


def _enviar_email(settings: Settings, payload: dict) -> None:
    destinatarios = [d.strip() for d in settings.alert_email_to.split(",") if d.strip()]
    if not destinatarios:
        return
    msg = EmailMessage()
    msg["Subject"] = (
        f"[Judicializacao] Nova acao {payload['faixa']} - {payload['comarca'] or payload['uf']}"
    )
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = ", ".join(destinatarios)
    valor = payload.get("valor_ressarcivel_estimado")
    corpo = (
        f"Processo: {payload['numero_processo']}\n"
        f"Tribunal: {payload['tribunal']} | Comarca: {payload['comarca']}\n"
        f"Faixa Tema 1.234: {payload['faixa']} ({payload['justica_competente']})\n"
        f"Percentual ressarcivel Uniao: {payload['percentual_ressarcivel']:.0%}\n"
        f"Valor potencialmente ressarcivel: R$ {valor:,.2f}\n"
        if valor is not None
        else f"Processo: {payload['numero_processo']}\n"
    )
    msg.set_content(corpo)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def _stub_slack(payload: dict) -> None:
    # TODO(v2): integrar webhook Slack.
    return


def _stub_whatsapp(payload: dict) -> None:
    # TODO(v2): integrar API WhatsApp Business.
    return

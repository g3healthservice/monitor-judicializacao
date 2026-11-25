"""Relogio de Brasilia. As datas do PNCP vem no horario de Brasilia, sem fuso,
e o GitHub Actions roda em UTC: comparar com datetime.now() la adiantaria 3 h."""
from __future__ import annotations

from datetime import datetime, timedelta


def agora_brasilia() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None, microsecond=0)
    except Exception:  # sem base de fusos no sistema
        return datetime.utcnow().replace(microsecond=0) - timedelta(hours=3)

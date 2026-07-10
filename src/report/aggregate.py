"""Agregacao de metricas por municipio para relatorios."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import mean
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..store.models import Movimentacao, Processo


def _parse_data(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    # DataJud usa dataAjuizamento no formato compacto YYYYMMDDHHMMSS (ou YYYYMMDD).
    if s.isdigit():
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None
    limpo = s.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(limpo[: len(fmt) + 6], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(limpo)
    except ValueError:
        return None


def _tempo_tramitacao_dias(session: Session, numero: str, ajuizamento: Optional[datetime]) -> Optional[int]:
    if ajuizamento is None:
        return None
    movs = session.exec(
        select(Movimentacao).where(Movimentacao.numero_processo == numero)
    ).all()
    datas = [d for d in (_parse_data(m.data) for m in movs) if d is not None]
    if not datas:
        return None
    ultima = max(datas)
    return max((ultima - ajuizamento).days, 0)


def agregar_municipio(session: Session, municipio_ibge: str) -> Dict:
    """Metricas de um municipio: contagem, faixas, dinheiro na mesa, tramitacao, top CIDs."""
    procs: List[Processo] = session.exec(
        select(Processo).where(Processo.municipio_ibge == municipio_ibge)
    ).all()

    faixas = Counter(p.faixa for p in procs)
    total_ressarcivel = sum(p.valor_ressarcivel_estimado or 0.0 for p in procs)
    total_causa = sum(p.valor_causa or 0.0 for p in procs)

    tempos = []
    for p in procs:
        t = _tempo_tramitacao_dias(session, p.numero_processo, _parse_data(p.data_ajuizamento))
        if t is not None:
            tempos.append(t)

    cids = Counter(p.cid for p in procs if p.cid)
    classes = Counter(p.classe for p in procs if p.classe)

    uf = procs[0].uf if procs else None
    comarca = procs[0].comarca if procs else None

    return {
        "municipio_ibge": municipio_ibge,
        "uf": uf,
        "comarca": comarca,
        "n_processos": len(procs),
        "distribuicao_faixa": dict(faixas),
        "valor_total_causa": round(total_causa, 2),
        "valor_total_ressarcivel": round(total_ressarcivel, 2),
        "tempo_medio_tramitacao_dias": round(mean(tempos), 1) if tempos else None,
        "top_cids": cids.most_common(10),
        "top_classes": classes.most_common(10),
        "processos": procs,
    }

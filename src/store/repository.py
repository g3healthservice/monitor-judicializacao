"""Repositorios: upsert idempotente de processos, checkpoints e alertas."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import Session, select

from .models import Alerta, Execucao, Movimentacao, Processo, TribunalCheckpoint


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------- Checkpoints -----------------------------------

def get_checkpoint(session: Session, tribunal: str) -> Optional[TribunalCheckpoint]:
    return session.get(TribunalCheckpoint, tribunal)


def upsert_checkpoint(
    session: Session,
    tribunal: str,
    ultimo_timestamp: Optional[str],
    ultimo_search_after: Optional[list],
) -> TribunalCheckpoint:
    cp = session.get(TribunalCheckpoint, tribunal)
    cursor = json.dumps(ultimo_search_after) if ultimo_search_after is not None else None
    if cp is None:
        cp = TribunalCheckpoint(
            tribunal=tribunal,
            ultimo_timestamp=ultimo_timestamp,
            ultimo_search_after=cursor,
        )
    else:
        cp.ultimo_timestamp = ultimo_timestamp
        cp.ultimo_search_after = cursor
        cp.atualizado_em = _now()
    session.add(cp)
    session.commit()
    session.refresh(cp)
    return cp


# ------------------------------- Processos -----------------------------------

def upsert_processo(session: Session, proc: Processo) -> tuple[Processo, bool]:
    """Insere ou atualiza. Retorna (processo, novo?).

    Idempotente: reexecucao nao duplica. Preserva criado_em em updates.
    """
    existente = session.get(Processo, proc.numero_processo)
    novo = existente is None
    if existente is None:
        proc.criado_em = _now()
        proc.atualizado_em = _now()
        session.add(proc)
    else:
        data = proc.model_dump(exclude={"criado_em"})
        for k, v in data.items():
            setattr(existente, k, v)
        existente.atualizado_em = _now()
        session.add(existente)
        proc = existente
    session.commit()
    session.refresh(proc)
    return proc, novo


def add_movimentacoes(session: Session, numero_processo: str, movs: List[dict]) -> int:
    """Adiciona movimentacoes ainda nao registradas (dedup por codigo+data)."""
    existentes = {
        (m.codigo, m.data)
        for m in session.exec(
            select(Movimentacao).where(Movimentacao.numero_processo == numero_processo)
        ).all()
    }
    inseridas = 0
    for m in movs:
        chave = (m.get("codigo"), m.get("data"))
        if chave in existentes:
            continue
        session.add(
            Movimentacao(
                numero_processo=numero_processo,
                codigo=m.get("codigo"),
                descricao=m.get("descricao"),
                data=m.get("data"),
            )
        )
        existentes.add(chave)
        inseridas += 1
    if inseridas:
        session.commit()
    return inseridas


# -------------------------------- Alertas ------------------------------------

def alerta_ja_existe(session: Session, dedup_key: str) -> bool:
    return session.exec(select(Alerta).where(Alerta.dedup_key == dedup_key)).first() is not None


def registrar_alerta(session: Session, alerta: Alerta) -> Optional[Alerta]:
    """Registra alerta se dedup_key inedito; senao retorna None."""
    if alerta_ja_existe(session, alerta.dedup_key):
        return None
    session.add(alerta)
    session.commit()
    session.refresh(alerta)
    return alerta


# ------------------------------- Execucoes -----------------------------------

def iniciar_execucao(session: Session) -> Execucao:
    ex = Execucao()
    session.add(ex)
    session.commit()
    session.refresh(ex)
    return ex


def finalizar_execucao(session: Session, ex: Execucao, **counters) -> Execucao:
    for k, v in counters.items():
        setattr(ex, k, v)
    ex.finalizado_em = _now()
    session.add(ex)
    session.commit()
    session.refresh(ex)
    return ex


def listar_processos_por_municipio(session: Session, municipio_ibge: str) -> List[Processo]:
    return session.exec(
        select(Processo).where(Processo.municipio_ibge == municipio_ibge)
    ).all()

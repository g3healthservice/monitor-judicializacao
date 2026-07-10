"""Modelos SQLModel. Nenhum campo de pessoa identificavel e persistido (LGPD).

Ver src/privacy/sanitize.py para a fronteira que garante isso na ingestao.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TribunalCheckpoint(SQLModel, table=True):
    """Ponto de retomada da ingestao incremental por tribunal."""

    __tablename__ = "tribunal_checkpoint"

    tribunal: str = Field(primary_key=True)
    ultimo_timestamp: Optional[str] = Field(default=None)  # ISO date do maior dataAjuizamento processado
    ultimo_search_after: Optional[str] = Field(default=None)  # JSON serializado do cursor ES
    atualizado_em: datetime = Field(default_factory=_now)


class Processo(SQLModel, table=True):
    """Metadado processual publico, ja classificado. Sem PII."""

    __tablename__ = "processo"

    numero_processo: str = Field(primary_key=True)
    tribunal: str = Field(index=True)
    grau: Optional[str] = None
    classe: Optional[str] = None
    assuntos: Optional[str] = None  # JSON: lista de codigos TPU
    orgao_julgador: Optional[str] = None
    comarca: Optional[str] = Field(default=None, index=True)
    municipio_ibge: Optional[str] = Field(default=None, index=True)
    uf: Optional[str] = Field(default=None, index=True)

    valor_causa: Optional[float] = None
    data_ajuizamento: Optional[str] = Field(default=None, index=True)

    # Enriquecimento (v1: heuristico / stub)
    farmaco: Optional[str] = None
    cid: Optional[str] = None
    oncologico: bool = Field(default=False)
    categoria: Optional[str] = Field(default=None, index=True)  # tipo de demanda (MEDICAMENTO, ONCOLOGICO...)
    assunto_principal: Optional[str] = None  # nome do assunto TPU

    # Classificacao Tema 1.234
    custo_anual_estimado: Optional[float] = None
    origem_estimativa: Optional[str] = None  # 'valor_causa' | 'cmed' | 'manual'
    faixa: Optional[str] = Field(default=None, index=True)
    percentual_ressarcivel: Optional[float] = None
    valor_ressarcivel_estimado: Optional[float] = None
    justica_competente: Optional[str] = None  # 'FEDERAL' | 'ESTADUAL' | 'LOCAL'

    criado_em: datetime = Field(default_factory=_now)
    atualizado_em: datetime = Field(default_factory=_now)


class Movimentacao(SQLModel, table=True):
    """Historico de movimentacoes de um processo (para tempo de tramitacao)."""

    __tablename__ = "movimentacao"

    id: Optional[int] = Field(default=None, primary_key=True)
    numero_processo: str = Field(index=True, foreign_key="processo.numero_processo")
    codigo: Optional[int] = None
    descricao: Optional[str] = None
    data: Optional[str] = None
    criado_em: datetime = Field(default_factory=_now)


class Alerta(SQLModel, table=True):
    """Alerta emitido. dedup_key unico garante nao-duplicidade."""

    __tablename__ = "alerta"

    id: Optional[int] = Field(default=None, primary_key=True)
    numero_processo: str = Field(index=True, foreign_key="processo.numero_processo")
    tipo: str = "NOVA_ACAO_ENQUADRAVEL"
    canal: str = "log"
    dedup_key: str = Field(unique=True)
    payload: Optional[str] = None  # JSON
    enviado_em: datetime = Field(default_factory=_now)


class Execucao(SQLModel, table=True):
    """Contadores por execucao (observabilidade)."""

    __tablename__ = "execucao"

    id: Optional[int] = Field(default=None, primary_key=True)
    iniciado_em: datetime = Field(default_factory=_now)
    finalizado_em: Optional[datetime] = None
    ingeridos: int = 0
    classificados: int = 0
    alertados: int = 0
    erros: int = 0
    detalhe: Optional[str] = None  # JSON livre

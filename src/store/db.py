"""Engine e sessao SQLModel/SQLite (com caminho para PostgreSQL)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlmodel import Session, SQLModel, create_engine

from ..config.settings import get_settings

_engine = None


def get_engine(database_url: Optional[str] = None):
    global _engine
    if _engine is None or database_url is not None:
        url = database_url or get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, echo=False, connect_args=connect_args)
    return _engine


def init_db(database_url: Optional[str] = None) -> None:
    """Cria as tabelas. Idempotente."""
    # Import garante que os modelos estejam registrados no metadata.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(get_engine(database_url))


@contextmanager
def get_session(database_url: Optional[str] = None) -> Iterator[Session]:
    with Session(get_engine(database_url)) as session:
        yield session

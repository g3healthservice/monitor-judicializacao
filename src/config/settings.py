"""Carregamento de configuracao: .env (segredos/parametros) + YAML (escopo)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuracao de runtime carregada de variaveis de ambiente / .env."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DataJud
    datajud_api_key: str = Field(default="", alias="DATAJUD_API_KEY")

    # Parametro economico
    salario_minimo: float = Field(default=1518.00, alias="SALARIO_MINIMO")

    # Banco
    database_url: str = Field(default="sqlite:///monitor.db", alias="DATABASE_URL")

    # Alertas e-mail
    alert_email_enabled: bool = Field(default=False, alias="ALERT_EMAIL_ENABLED")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    alert_email_to: str = Field(default="", alias="ALERT_EMAIL_TO")

    # Ingestao
    ingest_lookback_dias: int = Field(default=365, alias="INGEST_LOOKBACK_DIAS")
    ingest_page_size: int = Field(default=100, alias="INGEST_PAGE_SIZE")
    http_max_retries: int = Field(default=5, alias="HTTP_MAX_RETRIES")
    http_timeout_s: float = Field(default=30.0, alias="HTTP_TIMEOUT_S")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def limiar_estadual_valor(self) -> float:
        from .constants import LIMIAR_ESTADUAL_SM

        return LIMIAR_ESTADUAL_SM * self.salario_minimo

    @property
    def limiar_federal_valor(self) -> float:
        from .constants import LIMIAR_FEDERAL_SM

        return LIMIAR_FEDERAL_SM * self.salario_minimo


class Municipio(BaseModel):
    """Municipio/comarca monitorado (vem do YAML)."""

    nome: str
    codigo_ibge: str
    uf: str
    tribunal: str
    comarcas: List[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_municipios(path: Optional[Path] = None) -> List[Municipio]:
    """Carrega o escopo geografico de config/municipios.yaml."""
    path = path or (PROJECT_ROOT / "config" / "municipios.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return [Municipio(**m) for m in raw.get("municipios", [])]


def municipios_por_tribunal(municipios: List[Municipio]) -> dict:
    """Agrupa municipios por tribunal para orquestrar a ingestao."""
    agrupado: dict = {}
    for m in municipios:
        agrupado.setdefault(m.tribunal, []).append(m)
    return agrupado


class Tribunal(BaseModel):
    """Base de tribunal para ingestao em modo 'tribunal inteiro'."""

    sigla: str
    uf: str
    nome: str = ""


def load_tribunais(path: Optional[Path] = None) -> List[Tribunal]:
    """Carrega config/tribunais.yaml (27 TJs por padrao)."""
    path = path or (PROJECT_ROOT / "config" / "tribunais.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return [Tribunal(**t) for t in raw.get("tribunais", [])]

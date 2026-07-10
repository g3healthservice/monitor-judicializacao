"""Conectores de enriquecimento plugaveis (stubs na v1).

Cada conector implementa uma interface simples e pode ser ligado/desligado.
Na v1 retornam None/no-op; a arquitetura ja permite plugar as fontes reais:
  - CMED/PMVG (ANVISA): preco maximo -> estima custo anual (posologia x PMVG).
  - e-NatJus (CNJ): nota tecnica por CID/farmaco.
  - JudSaude (CNJ): validacao de competencia.
"""
from __future__ import annotations

from typing import Optional, Protocol

from pydantic import BaseModel


class EnriquecimentoResultado(BaseModel):
    farmaco: Optional[str] = None
    cid: Optional[str] = None
    oncologico: Optional[bool] = None
    custo_anual_estimado: Optional[float] = None
    origem_estimativa: Optional[str] = None  # 'cmed' | 'valor_causa' | 'manual'


class Conector(Protocol):
    nome: str

    def enriquecer(self, contexto: dict) -> EnriquecimentoResultado: ...


class CMEDConnector:
    """Stub CMED/PMVG. Na v1 nao consulta preco (retorna vazio)."""

    nome = "cmed"

    def __init__(self, ativo: bool = False):
        self.ativo = ativo

    def enriquecer(self, contexto: dict) -> EnriquecimentoResultado:
        if not self.ativo:
            return EnriquecimentoResultado()
        # TODO(v2): mapear farmaco -> PMVG -> custo_anual = PMVG * posologia_anual
        return EnriquecimentoResultado()


class ENatJusConnector:
    """Stub e-NatJus. Nota tecnica por CID/farmaco."""

    nome = "enatjus"

    def __init__(self, ativo: bool = False):
        self.ativo = ativo

    def enriquecer(self, contexto: dict) -> EnriquecimentoResultado:
        return EnriquecimentoResultado()


class JudSaudeConnector:
    """Stub JudSaude. Validacao de competencia."""

    nome = "judsaude"

    def __init__(self, ativo: bool = False):
        self.ativo = ativo

    def enriquecer(self, contexto: dict) -> EnriquecimentoResultado:
        return EnriquecimentoResultado()

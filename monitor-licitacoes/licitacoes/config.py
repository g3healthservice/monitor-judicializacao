"""Configuracao por variaveis de ambiente (todas com padrao razoavel)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


def _lista(valor: str) -> List[str]:
    return [v.strip() for v in valor.split(",") if v.strip()]


@dataclass
class Config:
    # Mesmo banco SQLite do monitor de judicializacao; tabelas com prefixo lic_.
    db_path: str = field(default_factory=lambda: os.getenv("LIC_DB_PATH", "monitor.db"))
    # Orgao/UASG monitorados. Inicial: Ministerio da Saude / DLOG (UASG 250005).
    cnpj_orgao: str = field(default_factory=lambda: os.getenv("LIC_CNPJ_ORGAO", "00394544000185"))
    uasgs: List[str] = field(default_factory=lambda: _lista(os.getenv("LIC_UASGS", "250005")))
    # Modalidades PNCP: 6 = pregao eletronico, 8 = dispensa, 9 = inexigibilidade.
    modalidades: List[str] = field(default_factory=lambda: _lista(os.getenv("LIC_MODALIDADES", "6")))
    # Na primeira execucao (sem checkpoint), quantos dias olhar para tras.
    lookback_dias: int = field(default_factory=lambda: int(os.getenv("LIC_LOOKBACK_DIAS", "60")))
    # Sobreposicao na coleta incremental, para nao perder atualizacoes tardias.
    sobreposicao_dias: int = field(default_factory=lambda: int(os.getenv("LIC_SOBREPOSICAO_DIAS", "2")))
    http_timeout_s: int = field(default_factory=lambda: int(os.getenv("LIC_HTTP_TIMEOUT_S", "120")))
    http_tentativas: int = field(default_factory=lambda: int(os.getenv("LIC_HTTP_TENTATIVAS", "4")))
    tamanho_pagina: int = field(default_factory=lambda: int(os.getenv("LIC_TAMANHO_PAGINA", "20")))
    max_arquivo_mb: int = field(default_factory=lambda: int(os.getenv("LIC_MAX_ARQUIVO_MB", "40")))
    sinais_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("LIC_SINAIS_PATH", str(Path(__file__).with_name("sinais.yaml")))
        )
    )
    saida_dir: Path = field(default_factory=lambda: Path(os.getenv("LIC_SAIDA_DIR", "out")))

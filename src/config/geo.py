"""Tabela IBGE (municipio -> nome, UF) e resolucao de municipio para qualquer codigo.

Permite o modo "Brasil inteiro": ingerir um TJ completo e agrupar os processos
por municipio a partir do codigoMunicipioIBGE da resposta, sem listar os 5.570
municipios a mao.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .settings import Municipio

_DATA = Path(__file__).resolve().parent / "data" / "municipios_ibge.json"


@lru_cache(maxsize=1)
def load_ibge_map() -> Dict[str, list]:
    """Mapa {codigo_ibge_7digitos: [nome, uf]} (fonte: IBGE/localidades)."""
    with open(_DATA, "r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_ibge(ibge, tribunal: str, ibge_map: Optional[Dict[str, list]] = None) -> Optional[Municipio]:
    """Constroi um Municipio para qualquer codigo IBGE (7 ou 6 digitos)."""
    if ibge is None:
        return None
    ibge_map = ibge_map if ibge_map is not None else load_ibge_map()
    ibge_str = str(ibge)
    par = ibge_map.get(ibge_str)
    if par is None:
        # tolera IBGE de 6 digitos (sem DV): tenta casar pelos 6 primeiros
        for cod, val in ibge_map.items():
            if cod[:6] == ibge_str[:6]:
                par, ibge_str = val, cod
                break
    if par is None:
        return None
    nome, uf = par
    return Municipio(nome=nome, codigo_ibge=ibge_str, uf=uf, tribunal=tribunal, comarcas=[])

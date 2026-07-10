"""Tabela IBGE (municipio -> nome, UF) e resolucao de municipio para qualquer codigo.

Permite o modo "Brasil inteiro": ingerir um TJ completo e agrupar os processos
por municipio a partir do codigoMunicipioIBGE da resposta, sem listar os 5.570
municipios a mao.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .settings import Municipio

_DATA = Path(__file__).resolve().parent / "data" / "municipios_ibge.json"

# IBGE das capitais (para foros "CENTRAL"/"REGIONAL"/"FORO CENTRAL", que designam
# a comarca da capital e nao um municipio homonimo).
_CAPITAIS = {
    "AC": "1200401", "AL": "2704302", "AM": "1302603", "AP": "1600303",
    "BA": "2927408", "CE": "2304400", "DF": "5300108", "ES": "3205309",
    "GO": "5208707", "MA": "2111300", "MG": "3106200", "MS": "5002704",
    "MT": "5103403", "PA": "1501402", "PB": "2507507", "PE": "2611606",
    "PI": "2211001", "PR": "4106902", "RJ": "3304557", "RN": "2408102",
    "RO": "1100205", "RR": "1400100", "RS": "4314902", "SC": "4205407",
    "SE": "2800308", "SP": "3550308", "TO": "1721000",
}


def _norm(s: str) -> str:
    """Normaliza para casar nomes: sem acento, maiusculo, so A-Z0-9."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", s.upper())


@lru_cache(maxsize=64)
def _indice_nomes_uf(uf: str) -> Dict[str, str]:
    """Indice {nome_normalizado: ibge} dos municipios de uma UF."""
    idx: Dict[str, str] = {}
    for ibge, (nome, u) in load_ibge_map().items():
        if u == uf:
            idx[_norm(nome)] = ibge
    return idx


# Tokens de foro que designam a capital, nao um municipio.
_FOROS_CAPITAL = {"CENTRAL", "FOROCENTRAL", "REGIONAL"}


def municipio_from_orgao(orgao_nome: str, uf: str, tribunal: str) -> Optional[Municipio]:
    """Deriva o municipio a partir do nome do orgao julgador (ex.: '... DE BAURU').

    Real: a base publica do DataJud frequentemente omite codigoMunicipioIBGE, mas
    o nome do orgao carrega o municipio ('N VARA ... DE <MUNICIPIO>').
    """
    if not orgao_nome or not uf:
        return None
    # Pega o trecho apos o ultimo ' DE ' (delimitador do foro/comarca).
    # So 'DE' (nao 'D' isolado, para nao quebrar em "D'Oeste").
    m = re.split(r"\bDE\b", orgao_nome.upper())
    candidato = m[-1].strip() if len(m) > 1 else orgao_nome.strip()
    chave = _norm(candidato)
    if not chave:
        return None

    idx = _indice_nomes_uf(uf)
    ibge = idx.get(chave)
    if ibge is None and chave in {_norm(x) for x in _FOROS_CAPITAL}:
        ibge = _CAPITAIS.get(uf)
    if ibge is None:
        return None
    nome = load_ibge_map()[ibge][0]
    return Municipio(nome=nome, codigo_ibge=ibge, uf=uf, tribunal=tribunal, comarcas=[candidato.title()])


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

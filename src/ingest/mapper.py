"""Mapeia um _source sanitizado do DataJud para um Processo classificavel."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..config.constants import CID_ONCOLOGICO_PREFIXOS
from ..config.settings import Municipio

_CID_RE = re.compile(r"\b([CD]\d{2})(?:\.\d+)?\b")


def _extrair_assuntos(source: dict) -> List[int]:
    out = []
    for a in source.get("assuntos", []) or []:
        cod = a.get("codigo")
        if cod is not None:
            try:
                out.append(int(cod))
            except (TypeError, ValueError):
                continue
    return out


def _texto_objeto(source: dict) -> str:
    """Concatena rotulos textuais (classe, assuntos, movimentos) para heuristicas.

    Nao inclui nada de pessoa; apenas nomes de classe/assunto/movimento.
    """
    partes: List[str] = []
    classe = source.get("classe") or {}
    if classe.get("nome"):
        partes.append(str(classe["nome"]))
    for a in source.get("assuntos", []) or []:
        if a.get("nome"):
            partes.append(str(a["nome"]))
    for m in source.get("movimentos", []) or []:
        if m.get("nome"):
            partes.append(str(m["nome"]))
    return " | ".join(partes)


def detectar_cid(texto: str) -> Optional[str]:
    m = _CID_RE.search(texto or "")
    return m.group(1) if m else None


def is_oncologico(cid: Optional[str], texto: str) -> bool:
    if cid and cid.upper().startswith(CID_ONCOLOGICO_PREFIXOS):
        return True
    t = (texto or "").lower()
    termos = ("oncolog", "neoplas", "cancer", "câncer", "quimioterap", "antineoplas")
    return any(termo in t for termo in termos)


def estimar_custo(
    source: dict,
    cmed_resultado=None,
) -> tuple[Optional[float], Optional[str]]:
    """Estima custo anual. v1: CMED (se ativo) tem prioridade; senao valor da causa.

    A base publica do DataJud raramente traz valorCausa; por isso o caminho
    principal em producao e o CMED. Retorna (custo, origem).
    """
    if cmed_resultado is not None and cmed_resultado.custo_anual_estimado is not None:
        return cmed_resultado.custo_anual_estimado, "cmed"
    vc = source.get("valorCausa")
    if vc is not None:
        try:
            return float(vc), "valor_causa"
        except (TypeError, ValueError):
            pass
    return None, None


def resolver_municipio(
    source: dict, municipios: List[Municipio]
) -> Optional[Municipio]:
    orgao = source.get("orgaoJulgador") or {}
    ibge = orgao.get("codigoMunicipioIBGE")
    if ibge is None:
        return None
    ibge_str = str(ibge)
    for m in municipios:
        # DataJud usa IBGE de 7 digitos; alguns registros usam 6 (sem DV).
        if m.codigo_ibge == ibge_str or m.codigo_ibge[:6] == ibge_str[:6]:
            return m
    return None


def map_source_to_processo_fields(
    source: dict,
    tribunal: str,
    municipio: Optional[Municipio] = None,
    cmed_resultado=None,
) -> Dict:
    """Constroi o dict de campos do Processo (sem tocar no banco)."""
    texto = _texto_objeto(source)
    cid = detectar_cid(texto)
    onc = is_oncologico(cid, texto)
    custo, origem = estimar_custo(source, cmed_resultado)
    orgao = source.get("orgaoJulgador") or {}
    ibge = orgao.get("codigoMunicipioIBGE")

    return {
        "numero_processo": source.get("numeroProcesso"),
        "tribunal": tribunal,
        "grau": source.get("grau"),
        "classe": (source.get("classe") or {}).get("nome"),
        "assuntos": json.dumps(_extrair_assuntos(source)),
        "orgao_julgador": orgao.get("nome"),
        "comarca": municipio.comarcas[0] if municipio and municipio.comarcas else orgao.get("nome"),
        "municipio_ibge": municipio.codigo_ibge if municipio else (str(ibge) if ibge else None),
        "uf": municipio.uf if municipio else None,
        "valor_causa": (float(source["valorCausa"]) if source.get("valorCausa") is not None else None),
        "data_ajuizamento": source.get("dataAjuizamento"),
        "farmaco": None,
        "cid": cid,
        "oncologico": onc,
        "custo_anual_estimado": custo,
        "origem_estimativa": origem,
    }


def extrair_movimentacoes(source: dict) -> List[dict]:
    out = []
    for m in source.get("movimentos", []) or []:
        out.append(
            {
                "codigo": m.get("codigo"),
                "descricao": m.get("nome"),
                "data": m.get("dataHora"),
            }
        )
    return out

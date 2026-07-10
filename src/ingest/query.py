"""Construcao das queries Elasticsearch para o DataJud."""
from __future__ import annotations

from typing import List, Optional

from ..config.constants import ASSUNTOS_SAUDE


def endpoint_tribunal(tribunal: str) -> str:
    """URL de busca do tribunal no padrao DataJud."""
    return f"https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal.lower()}/_search"


def build_query(
    assuntos: Optional[List[int]] = None,
    codigos_ibge: Optional[List[str]] = None,
    data_ajuizamento_gte: Optional[str] = None,
    page_size: int = 100,
    search_after: Optional[list] = None,
) -> dict:
    """Monta o corpo da busca.

    - Filtra por assuntos de saude (terms).
    - Opcionalmente filtra por municipio (codigoMunicipioIBGE do orgaoJulgador).
    - Ordena por dataAjuizamento + numeroProcesso (tie-break) para paginar via
      search_after de forma estavel.
    """
    assuntos = assuntos if assuntos is not None else ASSUNTOS_SAUDE

    filtros: List[dict] = [{"terms": {"assuntos.codigo": assuntos}}]

    if codigos_ibge:
        filtros.append(
            {"terms": {"orgaoJulgador.codigoMunicipioIBGE": [int(c) for c in codigos_ibge]}}
        )

    if data_ajuizamento_gte:
        filtros.append({"range": {"dataAjuizamento": {"gte": data_ajuizamento_gte}}})

    corpo: dict = {
        "size": page_size,
        "query": {"bool": {"filter": filtros}},
        "sort": [
            {"dataAjuizamento": {"order": "asc"}},
            {"numeroProcesso.keyword": {"order": "asc"}},
        ],
    }

    if search_after:
        corpo["search_after"] = search_after

    return corpo

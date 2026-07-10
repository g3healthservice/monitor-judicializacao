"""Construcao das queries Elasticsearch para o DataJud."""
from __future__ import annotations

from typing import List, Optional

from ..config.constants import ASSUNTOS_SAUDE


def endpoint_tribunal(tribunal: str) -> str:
    """URL de busca do tribunal no padrao DataJud."""
    return f"https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal.lower()}/_search"


def _data_compacta(s: Optional[str]) -> Optional[str]:
    """Normaliza a data para o formato do DataJud (YYYYMMDD...), sem separadores.

    O campo dataAjuizamento e armazenado como YYYYMMDDHHMMSS; um filtro range com
    data ISO ('2026-01-01') NAO casa. Aqui removemos separadores.
    """
    if not s:
        return None
    return "".join(ch for ch in str(s) if ch.isdigit())


def build_query(
    assuntos: Optional[List[int]] = None,
    codigos_ibge: Optional[List[str]] = None,
    data_ajuizamento_gte: Optional[str] = None,
    page_size: int = 100,
    search_after: Optional[list] = None,
    ordem: str = "desc",
) -> dict:
    """Monta o corpo da busca.

    - Filtra por assuntos de saude (terms).
    - Opcionalmente filtra por municipio (codigoMunicipioIBGE do orgaoJulgador).
    - Filtra por data no formato compacto do DataJud (para o lookback funcionar).
    - Ordena por dataAjuizamento (desc = mais recentes primeiro) + numeroProcesso
      para paginar via search_after de forma estavel.
    """
    assuntos = assuntos if assuntos is not None else ASSUNTOS_SAUDE

    filtros: List[dict] = [{"terms": {"assuntos.codigo": assuntos}}]

    if codigos_ibge:
        filtros.append(
            {"terms": {"orgaoJulgador.codigoMunicipioIBGE": [int(c) for c in codigos_ibge]}}
        )

    gte = _data_compacta(data_ajuizamento_gte)
    if gte:
        filtros.append({"range": {"dataAjuizamento": {"gte": gte}}})

    corpo: dict = {
        "size": page_size,
        "query": {"bool": {"filter": filtros}},
        "sort": [
            {"dataAjuizamento": {"order": ordem}},
            {"numeroProcesso.keyword": {"order": ordem}},
        ],
    }

    if search_after:
        corpo["search_after"] = search_after

    return corpo

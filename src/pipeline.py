"""Orquestracao end-to-end da ingestao incremental por tribunal.

Fluxo por hit: sanitize (LGPD) -> map -> enrich (stubs) -> classify (Tema 1.234)
-> upsert idempotente -> alerta (se enquadravel e inedito).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .alert.dispatcher import despachar_alerta
from .classify.tema1234 import classificar
from .config.constants import FAIXA_LOCAL
from .config.geo import load_ibge_map, municipio_from_orgao, resolve_ibge
from .config.logging_setup import get_logger
from .config.settings import Municipio, Settings, get_settings
from .enrich.connectors import CMEDConnector
from .ingest.client import DataJudClient
from .ingest.mapper import (
    extrair_movimentacoes,
    map_source_to_processo_fields,
    resolver_municipio,
)
from .privacy.sanitize import detectar_pii, sanitize
from .store import repository as repo
from .store.db import get_session
from .store.models import Processo

log = get_logger("pipeline")


def _data_inicial(settings: Settings, checkpoint_ts: Optional[str]) -> str:
    """Janela de busca (formato compacto YYYYMMDD). Foca em processos RECENTES.

    Usa o maior entre (checkpoint) e (hoje - lookback), para nao regredir para
    processos antigos e manter o radar em torno da demanda atual.
    """
    inicio = datetime.now(timezone.utc) - timedelta(days=settings.ingest_lookback_dias)
    janela = inicio.strftime("%Y%m%d")
    cp = "".join(ch for ch in str(checkpoint_ts) if ch.isdigit())[:8] if checkpoint_ts else None
    return max(janela, cp) if cp else janela


async def ingerir_tribunal(
    tribunal: str,
    municipios: Optional[List[Municipio]] = None,
    settings: Optional[Settings] = None,
    client: Optional[DataJudClient] = None,
    max_paginas: Optional[int] = None,
    database_url: Optional[str] = None,
    ibge_map: Optional[dict] = None,
    uf: Optional[str] = None,
) -> Dict[str, int]:
    """Ingesta incremental de um tribunal. Retorna contadores da execucao.

    Dois modos:
      - Lista de municipios (curado): filtra a query por codigoMunicipioIBGE.
      - municipios=None (tribunal inteiro): ingere todo o TJ e resolve o municipio
        pela tabela IBGE embutida (modo "Brasil inteiro").
    """
    settings = settings or get_settings()
    cmed = CMEDConnector(ativo=False)  # stub v1
    modo_inteiro = not municipios
    codigos_ibge = None if modo_inteiro else [m.codigo_ibge for m in municipios]
    if modo_inteiro and ibge_map is None:
        ibge_map = load_ibge_map()

    contadores = {"ingeridos": 0, "classificados": 0, "alertados": 0, "pii_flags": 0, "novos": 0}

    with get_session(database_url) as session:
        cp = repo.get_checkpoint(session, tribunal)
        # Radar de demanda recente: cada run varre a janela atual (ordem desc, mais
        # recentes primeiro) e faz upsert idempotente — nao resume cursor antigo.
        cursor = None
        data_gte = _data_inicial(settings, cp.ultimo_timestamp if cp else None)

        owns = client is None
        client = client or DataJudClient(tribunal, settings=settings)
        if owns:
            await client.__aenter__()
        try:
            maior_ts = cp.ultimo_timestamp if cp else None
            ultimo_cursor = cursor
            async for hit, sort_cursor in client.iterar_processos(
                codigos_ibge=codigos_ibge,
                data_ajuizamento_gte=data_gte,
                search_after=cursor,
                max_paginas=max_paginas,
            ):
                source = hit.get("_source", {})

                # Fronteira LGPD.
                flags = detectar_pii(source)
                if flags:
                    contadores["pii_flags"] += 1
                    log.warning(
                        "pii_detectada_descartada",
                        extra={"contexto": {"tribunal": tribunal, "campos": flags}},
                    )
                limpo = sanitize(source)

                if modo_inteiro:
                    orgao = limpo.get("orgaoJulgador") or {}
                    ibge = orgao.get("codigoMunicipioIBGE")
                    municipio = resolve_ibge(ibge, tribunal, ibge_map) if ibge else None
                    if municipio is None and uf:
                        # DataJud publico costuma omitir o IBGE: deriva do nome do orgao.
                        municipio = municipio_from_orgao(orgao.get("nome"), uf, tribunal)
                else:
                    municipio = resolver_municipio(limpo, municipios)
                cmed_res = cmed.enriquecer({"source": limpo})
                campos = map_source_to_processo_fields(
                    limpo, tribunal, municipio=municipio, cmed_resultado=cmed_res
                )
                if not campos.get("numero_processo"):
                    continue

                resultado = classificar(
                    custo_anual_estimado=campos["custo_anual_estimado"],
                    oncologico=campos["oncologico"],
                    settings=settings,
                )
                campos.update(
                    faixa=resultado.faixa,
                    percentual_ressarcivel=resultado.percentual_ressarcivel,
                    valor_ressarcivel_estimado=resultado.valor_ressarcivel_estimado,
                    justica_competente=resultado.justica_competente,
                )

                proc = Processo(**campos)
                proc, novo = repo.upsert_processo(session, proc)
                contadores["ingeridos"] += 1
                contadores["classificados"] += 1
                if novo:
                    contadores["novos"] += 1

                movs = extrair_movimentacoes(limpo)
                if movs:
                    repo.add_movimentacoes(session, proc.numero_processo, movs)

                # Alerta: nova acao enquadravel (com ressarcimento federal) em
                # municipio monitorado.
                enquadravel = (
                    municipio is not None
                    and resultado.faixa != FAIXA_LOCAL
                    and resultado.percentual_ressarcivel > 0
                )
                if novo and enquadravel:
                    if despachar_alerta(session, proc, settings=settings):
                        contadores["alertados"] += 1

                if proc.data_ajuizamento and (maior_ts is None or proc.data_ajuizamento > maior_ts):
                    maior_ts = proc.data_ajuizamento
                ultimo_cursor = sort_cursor

            repo.upsert_checkpoint(session, tribunal, maior_ts, ultimo_cursor)
        finally:
            if owns:
                await client.__aexit__(None, None, None)

    log.info("ingestao_tribunal_concluida", extra={"contexto": {"tribunal": tribunal, **contadores}})
    return contadores

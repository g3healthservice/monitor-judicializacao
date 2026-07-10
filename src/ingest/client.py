"""Cliente async do DataJud: autenticacao, retry/backoff e paginacao search_after."""
from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator, List, Optional

import httpx

from ..config.logging_setup import get_logger
from ..config.settings import Settings, get_settings
from .query import build_query, endpoint_tribunal

log = get_logger("ingest.client")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class DataJudError(RuntimeError):
    pass


class DataJudClient:
    """Cliente para uma base de tribunal do DataJud."""

    def __init__(
        self,
        tribunal: str,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.tribunal = tribunal
        self.settings = settings or get_settings()
        self.url = endpoint_tribunal(tribunal)
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "DataJudClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.http_timeout_s)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def _headers(self) -> dict:
        key = self.settings.datajud_api_key
        if not key:
            raise DataJudError(
                "DATAJUD_API_KEY ausente. Configure a chave publica do CNJ no .env."
            )
        return {
            "Authorization": f"APIKey {key}",
            "Content-Type": "application/json",
        }

    async def _post_com_retry(self, corpo: dict) -> dict:
        """POST com retry exponencial + jitter, respeitando Retry-After."""
        assert self._client is not None
        tentativas = self.settings.http_max_retries
        for tentativa in range(1, tentativas + 1):
            try:
                resp = await self._client.post(self.url, headers=self._headers, json=corpo)
            except httpx.TransportError as exc:
                # DataJud costuma derrubar conexoes (throttling): registra tipo p/ diagnostico.
                if tentativa == tentativas:
                    raise DataJudError(
                        f"Falha de transporte apos {tentativas} tentativas: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                log.warning(
                    "transporte_retry",
                    extra={"contexto": {"tribunal": self.tribunal, "tentativa": tentativa,
                                        "erro": type(exc).__name__}},
                )
                await self._backoff(tentativa)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if tentativa == tentativas:
                    raise DataJudError(
                        f"Status {resp.status_code} apos {tentativas} tentativas"
                    )
                retry_after = resp.headers.get("Retry-After")
                espera = float(retry_after) if retry_after and retry_after.isdigit() else None
                log.warning(
                    "rate_limit_ou_erro",
                    extra={"contexto": {"status": resp.status_code, "tribunal": self.tribunal}},
                )
                await self._backoff(tentativa, override=espera)
                continue

            if resp.status_code >= 400:
                raise DataJudError(f"Status {resp.status_code}: {resp.text[:300]}")

            return resp.json()

        raise DataJudError("Esgotadas as tentativas")

    async def _backoff(self, tentativa: int, override: Optional[float] = None) -> None:
        if override is not None:
            await asyncio.sleep(override)
            return
        base = min(2 ** tentativa, 30)
        await asyncio.sleep(base + random.uniform(0, 1))

    async def buscar_pagina(
        self,
        assuntos: Optional[List[int]] = None,
        codigos_ibge: Optional[List[str]] = None,
        data_ajuizamento_gte: Optional[str] = None,
        search_after: Optional[list] = None,
    ) -> dict:
        corpo = build_query(
            assuntos=assuntos,
            codigos_ibge=codigos_ibge,
            data_ajuizamento_gte=data_ajuizamento_gte,
            page_size=self.settings.ingest_page_size,
            search_after=search_after,
        )
        return await self._post_com_retry(corpo)

    async def iterar_processos(
        self,
        assuntos: Optional[List[int]] = None,
        codigos_ibge: Optional[List[str]] = None,
        data_ajuizamento_gte: Optional[str] = None,
        search_after: Optional[list] = None,
        max_paginas: Optional[int] = None,
    ) -> AsyncIterator[tuple[dict, list]]:
        """Itera hits pagina a pagina via search_after.

        Produz tuplas (hit, sort_cursor). O cursor do ultimo hit consumido serve
        de checkpoint para retomada incremental.
        """
        cursor = search_after
        paginas = 0
        while True:
            data = await self.buscar_pagina(
                assuntos=assuntos,
                codigos_ibge=codigos_ibge,
                data_ajuizamento_gte=data_ajuizamento_gte,
                search_after=cursor,
            )
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                return
            for h in hits:
                yield h, h.get("sort")
            cursor = hits[-1].get("sort")
            paginas += 1
            if max_paginas is not None and paginas >= max_paginas:
                return
            if len(hits) < self.settings.ingest_page_size:
                return

"""Cliente minimo da API publica do PNCP (sem autenticacao).

Endpoints usados:
  consulta: /api/consulta/v1/contratacoes/{publicacao|atualizacao}
  itens:    /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens
  arquivos: /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Dict, Iterator, List, Optional

from .config import Config

log = logging.getLogger(__name__)

BASE = "https://pncp.gov.br"
UA = "monitor-licitacoes/0.1 (+leitura de dados publicos)"


class ErroPNCP(Exception):
    pass


class ClientePNCP:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # -- HTTP -------------------------------------------------------------
    def _get(self, url: str) -> Optional[bytes]:
        ultimo = None
        for tentativa in range(self.cfg.http_tentativas):
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.http_timeout_s) as r:
                    if r.status == 204:
                        return None
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                ultimo = e
                if e.code < 500 and e.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                ultimo = e
            espera = 3 * (2 ** tentativa)
            log.warning("PNCP falhou (%s), nova tentativa em %ss: %s", ultimo, espera, url)
            time.sleep(espera)
        raise ErroPNCP(f"{url}: {ultimo}")

    def _json(self, url: str):
        corpo = self._get(url)
        if not corpo:
            return None
        return json.loads(corpo.decode("utf-8"))

    # -- Consultas --------------------------------------------------------
    def contratacoes(
        self,
        inicio: date,
        fim: date,
        modalidade: str,
        uasg: Optional[str] = None,
        por: str = "atualizacao",
    ) -> Iterator[Dict]:
        """Contratacoes do orgao no periodo, por data de atualizacao ou publicacao."""
        pagina = 1
        while True:
            params = {
                "dataInicial": inicio.strftime("%Y%m%d"),
                "dataFinal": fim.strftime("%Y%m%d"),
                "codigoModalidadeContratacao": modalidade,
                "cnpj": self.cfg.cnpj_orgao,
                "pagina": pagina,
                "tamanhoPagina": self.cfg.tamanho_pagina,
            }
            if uasg:
                params["codigoUnidadeAdministrativa"] = uasg
            url = f"{BASE}/api/consulta/v1/contratacoes/{por}?{urllib.parse.urlencode(params)}"
            dados = self._json(url)
            if not dados or not dados.get("data"):
                return
            yield from dados["data"]
            if pagina >= int(dados.get("totalPaginas") or 1):
                return
            pagina += 1

    def _compra(self, cnpj: str, ano: int, seq: int) -> str:
        return f"{BASE}/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}"

    def itens(self, cnpj: str, ano: int, seq: int) -> List[Dict]:
        saida, pagina = [], 1
        while True:
            dados = self._json(f"{self._compra(cnpj, ano, seq)}/itens?pagina={pagina}&tamanhoPagina=50")
            if not dados:
                return saida
            saida.extend(dados)
            if len(dados) < 50:
                return saida
            pagina += 1

    def arquivos(self, cnpj: str, ano: int, seq: int) -> List[Dict]:
        return self._json(f"{self._compra(cnpj, ano, seq)}/arquivos") or []

    def baixar(self, url: str) -> Optional[bytes]:
        return self._get(url)


def url_publica(cnpj: str, ano: int, seq: int) -> str:
    """Pagina da contratacao no portal do PNCP (para conferencia humana)."""
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"

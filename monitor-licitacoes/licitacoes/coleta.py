"""Etapa 1: coleta no PNCP, leitura dos documentos e aplicacao das regras de sinal."""
from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import db
from .config import Config
from .pncp import ClientePNCP, ErroPNCP, url_publica
from .sinais import Regras
from .texto import extrair_pdf, normalizar

log = logging.getLogger(__name__)

# NCM de medicamentos e produtos farmaceuticos (capitulo 30).
NCM_MEDICAMENTO = ("3001", "3002", "3003", "3004", "3006")

_RE_PREGAO = re.compile(r"(?:pregao|\bpe\b)[^0-9]{0,40}?(9\d{4,5})\s*[/_\-]\s*(20\d{2})")


def agora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def numero_pregao(*textos: Optional[str]) -> Optional[str]:
    """Numero do pregao citado no objeto ou no titulo de um arquivo.

    Normaliza digitacoes com zero a mais (900114/2026 -> 90114/2026), comuns no
    campo objeto do PNCP.
    """
    for t in textos:
        if not t:
            continue
        m = _RE_PREGAO.search(normalizar(t))
        if m:
            num, ano = m.group(1), m.group(2)
            if len(num) == 6 and num.startswith("900"):
                num = "9" + num[2:]
            return f"{num}/{ano}"
    return None


# Descricao padrao CATMAT de medicamento (usada quando o item vem sem NCM).
_RE_DESC_MEDICAMENTO = re.compile(r"forma\s*farmaceutica|principio\s*ativo|\bconcentracao\s*:|\bdosagem\s*:")


def eh_medicamento(itens: List[Dict], objeto: str) -> bool:
    if any(str(i.get("ncmNbsCodigo") or "").startswith(NCM_MEDICAMENTO) for i in itens):
        return True
    if any(_RE_DESC_MEDICAMENTO.search(normalizar(i.get("descricao") or "")) for i in itens):
        return True
    return "medicament" in normalizar(objeto or "")


_LIMITE_PDF = 80 * 2**20


def _pdfs_de(conteudo: bytes) -> List[Tuple[str, bytes]]:
    """PDFs contidos no arquivo baixado: o proprio PDF ou os PDFs de um ZIP/RAR/7z."""
    if conteudo[:5] == b"%PDF-":
        return [("", conteudo)]
    if conteudo[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
                return [
                    (n, z.read(n))
                    for n in z.namelist()
                    if n.lower().endswith(".pdf") and z.getinfo(n).file_size < _LIMITE_PDF
                ]
        except zipfile.BadZipFile:
            return []
    if conteudo[:4] == b"Rar!" or conteudo[:6] == b"7z\xbc\xaf\x27\x1c":
        return _pdfs_bsdtar(conteudo)
    return []


def _pdfs_bsdtar(conteudo: bytes) -> List[Tuple[str, bytes]]:
    """Extrai PDFs de RAR/7z com o bsdtar (pacote libarchive-tools)."""
    if not shutil.which("bsdtar"):
        raise ValueError("arquivo RAR/7z: instale o bsdtar (libarchive-tools) para ler")
    with tempfile.TemporaryDirectory() as d:
        pacote = Path(d) / "pacote"
        pacote.write_bytes(conteudo)
        destino = Path(d) / "x"
        destino.mkdir()
        # bsdtar recusa caminhos absolutos e ".." por padrao.
        r = subprocess.run(["bsdtar", "-xf", str(pacote), "-C", str(destino), "--no-same-owner"],
                           capture_output=True, timeout=300)
        if r.returncode != 0 and not any(destino.rglob("*")):
            raise ValueError(f"falha ao abrir arquivo compactado: {r.stderr.decode(errors='replace')[:200]}")
        return [
            (str(p.relative_to(destino)), p.read_bytes())
            for p in sorted(destino.rglob("*"))
            if p.is_file() and p.suffix.lower() == ".pdf" and p.stat().st_size < _LIMITE_PDF
        ]


class Coletor:
    def __init__(self, cfg: Config, regras: Regras, con: sqlite3.Connection,
                 cliente: Optional[ClientePNCP] = None):
        self.cfg = cfg
        self.regras = regras
        self.con = con
        self.cliente = cliente or ClientePNCP(cfg)
        self.stats = {"lidas": 0, "processadas": 0, "medicamentos": 0, "arquivos_lidos": 0,
                      "candidatas_novas": 0, "erros": 0}

    # -- Varredura --------------------------------------------------------
    def coletar(self, desde: Optional[date] = None, ate: Optional[date] = None,
                por: str = "atualizacao", reprocessar: bool = False) -> Dict[str, int]:
        ate = ate or date.today()
        for uasg in self.cfg.uasgs:
            for mod in self.cfg.modalidades:
                chave = f"{por}:{self.cfg.cnpj_orgao}:{uasg}:{mod}"
                ini = desde or self._inicio(chave, ate)
                log.info("Coletando UASG %s modalidade %s de %s a %s (%s)", uasg, mod, ini, ate, por)
                for janela_ini, janela_fim in _janelas(ini, ate, dias=30):
                    for reg in self.cliente.contratacoes(janela_ini, janela_fim, mod, uasg, por=por):
                        self.stats["lidas"] += 1
                        try:
                            self._tratar(reg, reprocessar)
                        except ErroPNCP as e:
                            self.stats["erros"] += 1
                            log.error("Falha em %s: %s", reg.get("numeroControlePNCP"), e)
                if desde is None:
                    with db.transacao(self.con):
                        db.gravar_checkpoint(self.con, chave, ate.isoformat())
        return self.stats

    def _inicio(self, chave: str, ate: date) -> date:
        ck = db.ler_checkpoint(self.con, chave)
        if ck:
            return date.fromisoformat(ck) - timedelta(days=self.cfg.sobreposicao_dias)
        return ate - timedelta(days=self.cfg.lookback_dias)

    # -- Uma contratacao --------------------------------------------------
    def _tratar(self, reg: Dict, reprocessar: bool) -> None:
        nc = reg["numeroControlePNCP"]
        versao = reg.get("dataAtualizacaoGlobal") or reg.get("dataAtualizacao")
        atual = self.con.execute(
            "SELECT avaliada_versao, candidata FROM lic_compra WHERE numero_controle = ?", (nc,)
        ).fetchone()
        self._gravar_compra(reg)
        if atual and atual["avaliada_versao"] == versao and not reprocessar:
            return
        self.processar(nc, reg, era_candidata=bool(atual and atual["candidata"]))

    def _gravar_compra(self, reg: Dict) -> None:
        un = reg.get("unidadeOrgao") or {}
        org = reg.get("orgaoEntidade") or {}
        cnpj, ano, seq = org.get("cnpj"), reg["anoCompra"], reg["sequencialCompra"]
        uasg = un.get("codigoUnidade")
        ts = agora()
        with db.transacao(self.con):
            self.con.execute(
                """
                INSERT INTO lic_compra (numero_controle, cnpj, ano, sequencial, uasg, unidade_nome,
                    numero_compra, numero_pregao, identificador, modalidade, objeto, situacao,
                    processo, srp, data_publicacao, data_abertura, data_encerramento,
                    data_atualizacao, link_origem, url_pncp, primeira_leitura, ultima_leitura)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(numero_controle) DO UPDATE SET
                    unidade_nome=excluded.unidade_nome, objeto=excluded.objeto,
                    situacao=excluded.situacao, processo=excluded.processo,
                    numero_pregao=COALESCE(excluded.numero_pregao, lic_compra.numero_pregao),
                    data_abertura=excluded.data_abertura,
                    data_encerramento=excluded.data_encerramento,
                    data_atualizacao=excluded.data_atualizacao,
                    link_origem=excluded.link_origem, ultima_leitura=excluded.ultima_leitura
                """,
                (
                    reg["numeroControlePNCP"], cnpj, ano, seq, uasg, un.get("nomeUnidade"),
                    reg.get("numeroCompra"), numero_pregao(reg.get("objetoCompra")),
                    f"{uasg}-{reg.get('numeroCompra')}-{ano}", reg.get("modalidadeNome"),
                    reg.get("objetoCompra"), reg.get("situacaoCompraNome"), reg.get("processo"),
                    int(bool(reg.get("srp"))), reg.get("dataPublicacaoPncp"),
                    reg.get("dataAberturaProposta"), reg.get("dataEncerramentoProposta"),
                    reg.get("dataAtualizacaoGlobal") or reg.get("dataAtualizacao"),
                    reg.get("linkSistemaOrigem"), url_publica(cnpj, ano, seq), ts, ts,
                ),
            )

    def processar(self, nc: str, reg: Dict, era_candidata: bool = False) -> None:
        self.stats["processadas"] += 1
        cnpj = (reg.get("orgaoEntidade") or {}).get("cnpj")
        ano, seq = reg["anoCompra"], reg["sequencialCompra"]
        objeto = reg.get("objetoCompra") or ""
        ts = agora()

        itens = self.cliente.itens(cnpj, ano, seq)
        self._gravar_itens(nc, itens, ts)
        medicamento = eh_medicamento(itens, objeto)

        titulos: List[str] = []
        if medicamento:
            self.stats["medicamentos"] += 1
            self._sinais_documento(nc, 0, objeto, None, f"{url_publica(cnpj, ano, seq)} (objeto)", ts)
            for arq in self.cliente.arquivos(cnpj, ano, seq):
                titulos.append(arq.get("titulo") or "")
                self._ler_arquivo(nc, arq, ts)

        ids = [r["sinal_id"] for r in self.con.execute(
            "SELECT DISTINCT sinal_id FROM lic_sinal WHERE numero_controle = ?", (nc,))]
        nota = self.regras.pontuar(ids) if medicamento else {"pontuacao": 0, "tem_alto": False, "candidata": False}
        if nota["candidata"] and not era_candidata:
            self.stats["candidatas_novas"] += 1
        with db.transacao(self.con):
            self.con.execute(
                """UPDATE lic_compra SET eh_medicamento=?, pontuacao=?, tem_alto=?, candidata=?,
                       numero_pregao=COALESCE(numero_pregao, ?), avaliada_em=?, avaliada_versao=?
                   WHERE numero_controle=?""",
                (int(medicamento), nota["pontuacao"], int(nota["tem_alto"]), int(nota["candidata"]),
                 numero_pregao(*titulos), ts,
                 reg.get("dataAtualizacaoGlobal") or reg.get("dataAtualizacao"), nc),
            )
        log.info("%s | med=%s pontos=%s candidata=%s | %s", nc, medicamento,
                 nota["pontuacao"], nota["candidata"], objeto[:70])

    def _gravar_itens(self, nc: str, itens: Iterable[Dict], ts: str) -> None:
        with db.transacao(self.con):
            for i in itens:
                self.con.execute(
                    """INSERT INTO lic_item (numero_controle, numero_item, descricao, quantidade,
                           unidade, ncm, catmat, valor_unitario_estimado, valor_total,
                           orcamento_sigiloso, situacao, tem_resultado, data_atualizacao, lido_em)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(numero_controle, numero_item) DO UPDATE SET
                           descricao=excluded.descricao, quantidade=excluded.quantidade,
                           unidade=excluded.unidade, ncm=excluded.ncm, catmat=excluded.catmat,
                           valor_unitario_estimado=excluded.valor_unitario_estimado,
                           valor_total=excluded.valor_total,
                           orcamento_sigiloso=excluded.orcamento_sigiloso,
                           situacao=excluded.situacao, tem_resultado=excluded.tem_resultado,
                           data_atualizacao=excluded.data_atualizacao, lido_em=excluded.lido_em""",
                    (nc, i.get("numeroItem"), i.get("descricao"), i.get("quantidade"),
                     i.get("unidadeMedida"), i.get("ncmNbsCodigo"),
                     str(i["catalogoCodigoItem"]) if i.get("catalogoCodigoItem") else None,
                     i.get("valorUnitarioEstimado"), i.get("valorTotal"),
                     int(bool(i.get("orcamentoSigiloso"))), i.get("situacaoCompraItemNome"),
                     int(bool(i.get("temResultado"))), i.get("dataAtualizacao"), ts),
                )

    def _ler_arquivo(self, nc: str, arq: Dict, ts: str) -> None:
        seq_doc = int(arq["sequencialDocumento"])
        url = arq.get("url") or arq.get("uri")
        ja = self.con.execute(
            "SELECT lido_em, data_publicacao, erro FROM lic_arquivo WHERE numero_controle=? AND sequencial_documento=?",
            (nc, seq_doc)).fetchone()
        if ja and ja["lido_em"] and not ja["erro"] and ja["data_publicacao"] == arq.get("dataPublicacaoPncp"):
            return  # mesmo documento ja lido
        if not arq.get("statusAtivo", True):
            self._gravar_arquivo(nc, arq, None, 0, 0, ts, "documento inativo")
            return

        erro, sha, paginas, chars = None, None, 0, 0
        try:
            conteudo = self.cliente.baixar(url) or b""
            if len(conteudo) > self.cfg.max_arquivo_mb * 2**20:
                raise ValueError(f"arquivo maior que {self.cfg.max_arquivo_mb} MB")
            sha = hashlib.sha256(conteudo).hexdigest()
            pdfs = _pdfs_de(conteudo)
            if not pdfs:
                raise ValueError("formato nao suportado (nao e PDF nem ZIP com PDF)")
            with db.transacao(self.con):
                self.con.execute(
                    "DELETE FROM lic_sinal WHERE numero_controle=? AND sequencial_documento=?",
                    (nc, seq_doc))
            for nome, pdf in pdfs:
                texto, inicios = extrair_pdf(pdf)
                paginas += len(inicios)
                chars += len(texto)
                fonte = f"{url}" + (f" [{nome}]" if nome else "")
                self._sinais_documento(nc, seq_doc, texto, inicios, fonte, ts, substituir=False)
            if chars < 200:
                erro = "pouco texto extraido (PDF digitalizado?)"
            self.stats["arquivos_lidos"] += 1
        except (ErroPNCP, ValueError) as e:
            erro = str(e)[:500]
            self.stats["erros"] += 1
            log.warning("Arquivo %s/%s nao lido: %s", nc, seq_doc, erro)
        except Exception as e:  # PDF corrompido etc.: registra e segue
            erro = f"falha na leitura: {e}"[:500]
            self.stats["erros"] += 1
            log.warning("Arquivo %s/%s nao lido: %s", nc, seq_doc, erro)
        self._gravar_arquivo(nc, arq, sha, paginas, chars, ts, erro)

    def _gravar_arquivo(self, nc, arq, sha, paginas, chars, ts, erro) -> None:
        with db.transacao(self.con):
            self.con.execute(
                """INSERT INTO lic_arquivo (numero_controle, sequencial_documento, titulo, tipo, url,
                       data_publicacao, ativo, sha256, paginas, caracteres, lido_em, erro)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(numero_controle, sequencial_documento) DO UPDATE SET
                       titulo=excluded.titulo, tipo=excluded.tipo, url=excluded.url,
                       data_publicacao=excluded.data_publicacao, ativo=excluded.ativo,
                       sha256=excluded.sha256, paginas=excluded.paginas,
                       caracteres=excluded.caracteres, lido_em=excluded.lido_em, erro=excluded.erro""",
                (nc, int(arq["sequencialDocumento"]), arq.get("titulo"), arq.get("tipoDocumentoNome"),
                 arq.get("url") or arq.get("uri"), arq.get("dataPublicacaoPncp"),
                 int(bool(arq.get("statusAtivo", True))), sha, paginas, chars, ts, erro),
            )

    def _sinais_documento(self, nc: str, seq_doc: int, texto: str, inicios, fonte: str,
                          ts: str, substituir: bool = True) -> None:
        achados = self.regras.buscar(texto, inicios)
        with db.transacao(self.con):
            if substituir:
                self.con.execute(
                    "DELETE FROM lic_sinal WHERE numero_controle=? AND sequencial_documento=?",
                    (nc, seq_doc))
            for a in achados:
                # Num ZIP com varios PDFs, fica a primeira ocorrencia e soma as demais.
                self.con.execute(
                    """INSERT INTO lic_sinal (numero_controle, sequencial_documento, sinal_id, nivel,
                           peso, trecho, pagina, ocorrencias, fonte_url, lido_em)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(numero_controle, sequencial_documento, sinal_id) DO UPDATE SET
                           ocorrencias = lic_sinal.ocorrencias + excluded.ocorrencias""",
                    (nc, seq_doc, a.sinal_id, a.nivel, a.peso, a.trecho, a.pagina, a.ocorrencias,
                     fonte, ts),
                )


def _janelas(ini: date, fim: date, dias: int) -> Iterable[Tuple[date, date]]:
    """Divide o periodo em janelas (a API do PNCP fica lenta em periodos longos)."""
    atual = ini
    while atual <= fim:
        proximo = min(fim, atual + timedelta(days=dias - 1))
        yield atual, proximo
        atual = proximo + timedelta(days=1)

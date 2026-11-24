"""Etapa 4b: alertas por e-mail (e Telegram, quando configurado).

Avisa quando:
  1. sai edital novo com sinais de produto sem registro (contratacao candidata nova);
  2. um item acompanhado fracassa, fica deserto ou e cancelado (e demais mudancas
     de situacao, vencedor e ata, como informacao);
  3. o fim das propostas ou um prazo informado (diligencia) esta a menos de 48 h;
  4. o valor de referencia de um item muda no PNCP.

"Acompanhados" = contratacoes candidatas + contratacoes com item em cotacoes.csv.
Cada alerta tem uma chave unica em lic_alerta e e enviado uma vez so. Todos os
alertas novos de uma rodada vao juntos numa unica mensagem.

Configuracao (variaveis de ambiente; as mesmas SMTP_* do monitor de judicializacao):
  SMTP_HOST, SMTP_PORT (587 STARTTLS, 465 SSL), SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
  ALERT_EMAIL_TO (lista separada por virgula)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (opcional)
Sem canal configurado, os alertas ficam pendentes no banco e saem no primeiro
envio depois que um canal for configurado (prazos ja vencidos sao descartados).
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import smtplib
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Dict, List, Optional

from . import db

log = logging.getLogger(__name__)

SEM_SUCESSO = {"Fracassado", "Deserto", "Anulado/Revogado/Cancelado"}
TITULOS = {
    "edital_novo": "Editais novos com sinais de produto sem registro",
    "sem_sucesso": "Itens fracassados, desertos ou cancelados",
    "prazo": "Prazos a menos de 48 horas",
    "referencia": "Mudanças no preço de referência",
    "situacao": "Outras mudanças (homologação, vencedor, ata)",
}
ORDEM = ["prazo", "edital_novo", "sem_sucesso", "referencia", "situacao"]


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def acompanhadas(con: sqlite3.Connection, cotacoes: Dict[tuple, Dict]) -> set:
    ids = {r["numero_controle"] for r in con.execute("SELECT numero_controle FROM lic_compra WHERE candidata = 1")}
    cotadas = sorted({c for (c, _i) in cotacoes})
    if cotadas:
        ids |= {r["numero_controle"] for r in con.execute(
            f"SELECT numero_controle FROM lic_compra WHERE identificador IN ({','.join('?' * len(cotadas))})",
            cotadas)}
    return ids


def _ref(c) -> str:
    return f"PE {c['numero_pregao']}" if c["numero_pregao"] else f"contratação {c['identificador']}"


def _inserir(con, chave, tipo, nc, item, titulo, detalhe, url, ts, prazo=None) -> bool:
    cur = con.execute(
        """INSERT OR IGNORE INTO lic_alerta (chave, tipo, numero_controle, numero_item, titulo, detalhe,
               url, prazo, criado_em) VALUES (?,?,?,?,?,?,?,?,?)""",
        (chave, tipo, nc, item, titulo, detalhe, url, prazo, ts))
    return cur.rowcount > 0


def gerar(con: sqlite3.Connection, cotacoes: Dict[tuple, Dict], prazo_horas: float = 48,
          agora: Optional[datetime] = None, dias_edital_novo: int = 7) -> int:
    """Grava em lic_alerta os alertas novos. Retorna quantos foram criados."""
    agora = agora or _agora()
    ts = agora.isoformat()
    novos = 0
    alvo = acompanhadas(con, cotacoes)
    with db.transacao(con):
        # 1. Edital novo: candidata com propostas abertas ou publicada ha poucos dias.
        limite_pub = (agora - timedelta(days=dias_edital_novo)).isoformat()
        for c in con.execute(
                """SELECT * FROM lic_compra WHERE candidata = 1
                   AND (data_encerramento > ? OR data_publicacao >= ?)""", (ts, limite_pub)).fetchall():
            trecho = con.execute(
                """SELECT trecho FROM lic_sinal WHERE numero_controle = ?
                   ORDER BY peso DESC, sequencial_documento DESC LIMIT 1""", (c["numero_controle"],)).fetchone()
            detalhe = (f"{c['pontuacao']} pontos. Propostas até {(c['data_encerramento'] or '—')[:16].replace('T', ' ')}. "
                       f"{c['objeto'][:220]}" + (f" — Trecho: “{trecho['trecho'][:220]}”" if trecho else ""))
            novos += _inserir(con, f"edital_novo:{c['numero_controle']}", "edital_novo", c["numero_controle"],
                              None, f"{_ref(c)} ({c['identificador']})", detalhe, c["url_pncp"], ts)

        # 2 e 4. Eventos de itens acompanhados.
        for e in con.execute(
                """SELECT e.*, c.numero_pregao, c.identificador, c.url_pncp, i.descricao
                   FROM lic_evento e JOIN lic_compra c USING (numero_controle)
                   LEFT JOIN lic_item i ON i.numero_controle = e.numero_controle AND i.numero_item = e.numero_item
                   WHERE e.id > COALESCE((SELECT CAST(valor AS INTEGER) FROM lic_checkpoint
                                          WHERE chave = 'alertas:ultimo_evento'), 0)
                   ORDER BY e.id""").fetchall():
            if e["numero_controle"] not in alvo:
                continue
            item = f"item {e['numero_item']} ({(e['descricao'] or '')[:50]})" if e["numero_item"] else ""
            if e["tipo"] == "situacao":
                tipo = "sem_sucesso" if e["para"] in SEM_SUCESSO else "situacao"
                detalhe = f"{item}: {e['de']} → {e['para']}"
            elif e["tipo"] == "referencia":
                tipo = "referencia"
                detalhe = f"{item}: R$ {e['de']} → R$ {e['para']}"
            elif e["tipo"] == "resultado":
                tipo, detalhe = "situacao", f"{item}: vencedor {e['para']} ({e['detalhe']})"
            else:
                tipo, detalhe = "situacao", f"{e['para']} ({e['detalhe']})"
            novos += _inserir(con, f"evento:{e['id']}", tipo, e["numero_controle"], e["numero_item"],
                              _ref(e), detalhe, e["url_pncp"], ts)
        ultimo = con.execute("SELECT MAX(id) AS m FROM lic_evento").fetchone()["m"]
        if ultimo:
            db.gravar_checkpoint(con, "alertas:ultimo_evento", str(ultimo))

        # 3. Prazos a menos de prazo_horas: fim das propostas e prazos informados.
        ate = agora + timedelta(hours=prazo_horas)
        for c in con.execute("SELECT * FROM lic_compra WHERE data_encerramento > ? AND data_encerramento <= ?",
                             (ts, ate.isoformat())).fetchall():
            if c["numero_controle"] in alvo:
                novos += _inserir(con, f"prazo:{c['numero_controle']}:0:{c['data_encerramento']}", "prazo",
                                  c["numero_controle"], None, _ref(c),
                                  f"Fim das propostas em {c['data_encerramento'][:16].replace('T', ' ')}. "
                                  f"{c['objeto'][:160]}", c["url_pncp"], ts, c["data_encerramento"])
        for (ident, n), cot in cotacoes.items():
            pm = (cot.get("prazo_manual") or "").strip()
            if not pm:
                continue
            try:
                quando = datetime.fromisoformat(pm[:19])
            except ValueError:
                continue
            if not (agora < quando <= ate):
                continue
            c = con.execute("SELECT * FROM lic_compra WHERE identificador = ?", (ident,)).fetchone()
            ref = _ref(c) if c else ident
            novos += _inserir(con, f"prazo:{ident}:{n}:{pm}", "prazo", c["numero_controle"] if c else None, n,
                              ref, f"item {n} ({cot.get('medicamento') or ''}): prazo em {pm.replace('T', ' ')}. "
                              f"{(cot.get('observacao') or '')[:160]}", c["url_pncp"] if c else None, ts, pm)
    return novos


def pendentes(con: sqlite3.Connection, agora: Optional[datetime] = None) -> List[Dict]:
    """Alertas ainda nao enviados; descarta prazos que venceram antes do envio."""
    agora = agora or _agora()
    with db.transacao(con):
        con.execute("""UPDATE lic_alerta SET enviado_em = 'descartado: prazo vencido'
                       WHERE enviado_em IS NULL AND tipo = 'prazo' AND prazo < ?""", (agora.isoformat(),))
    linhas = [dict(r) for r in con.execute("SELECT * FROM lic_alerta WHERE enviado_em IS NULL ORDER BY criado_em")]
    return sorted(linhas, key=lambda a: (ORDEM.index(a["tipo"]) if a["tipo"] in ORDEM else 99, a["criado_em"]))


# -- Mensagem ---------------------------------------------------------------

def assunto(lista: List[Dict]) -> str:
    cont = {t: sum(1 for a in lista if a["tipo"] == t) for t in ORDEM}
    partes = []
    if cont["prazo"]:
        partes.append(f"{cont['prazo']} prazo(s) em 48 h")
    if cont["edital_novo"]:
        partes.append(f"{cont['edital_novo']} edital(is) novo(s)")
    if cont["sem_sucesso"]:
        partes.append(f"{cont['sem_sucesso']} item(ns) sem sucesso")
    if cont["referencia"]:
        partes.append(f"{cont['referencia']} referência(s) alterada(s)")
    if cont["situacao"]:
        partes.append(f"{cont['situacao']} outra(s) mudança(s)")
    return "Licitações sem registro: " + (", ".join(partes) or "sem novidades")


def texto(lista: List[Dict]) -> str:
    if not lista:
        return "Nenhum alerta novo."
    linhas = []
    for t in ORDEM:
        grupo = [a for a in lista if a["tipo"] == t]
        if not grupo:
            continue
        linhas.append(f"== {TITULOS[t]} ({len(grupo)})")
        for a in grupo:
            linhas.append(f"- {a['titulo']}: {a['detalhe']}" + (f"\n  {a['url']}" if a["url"] else ""))
        linhas.append("")
    return "\n".join(linhas).strip()


def html(lista: List[Dict], extra_html: str = "") -> str:
    blocos = []
    for t in ORDEM:
        grupo = [a for a in lista if a["tipo"] == t]
        if not grupo:
            continue
        itens = "".join(
            f"<li><b>{_html.escape(a['titulo'])}</b>: {_html.escape(a['detalhe'])}"
            + (f" — <a href='{_html.escape(a['url'])}'>PNCP</a>" if a["url"] else "") + "</li>"
            for a in grupo)
        cor = "#b00020" if t in ("prazo", "sem_sucesso") else "#222"
        blocos.append(f"<h3 style='color:{cor};margin:16px 0 4px'>{TITULOS[t]} ({len(grupo)})</h3><ul>{itens}</ul>")
    if extra_html:
        blocos.append("<h3 style='margin:16px 0 4px'>Cenários dos itens cotados citados acima</h3>" + extra_html)
    blocos.append("<p style='color:#666;font-size:12px'>Monitor de licitações (etapa 4). Dados públicos do PNCP; "
                  "preços indianos e referências de mercado vêm de cotacoes.csv. A decisão de participar é sua.</p>")
    return "<div style='font-family:Arial,sans-serif;font-size:14px'>" + "".join(blocos) + "</div>"


# -- Canais -----------------------------------------------------------------

def canais_configurados() -> List[str]:
    canais = []
    if os.getenv("SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"):
        canais.append("email")
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        canais.append("telegram")
    return canais


def enviar_email(assunto_: str, corpo_texto: str, corpo_html: str) -> None:
    destinos = [d.strip() for d in os.environ["ALERT_EMAIL_TO"].split(",") if d.strip()]
    usuario = os.getenv("SMTP_USER", "")
    msg = EmailMessage()
    msg["Subject"] = assunto_
    msg["From"] = os.getenv("SMTP_FROM") or usuario
    msg["To"] = ", ".join(destinos)
    msg.set_content(corpo_texto)
    msg.add_alternative(corpo_html, subtype="html")
    porta = int(os.getenv("SMTP_PORT", "587"))
    host = os.environ["SMTP_HOST"]
    cls = smtplib.SMTP_SSL if porta == 465 else smtplib.SMTP
    with cls(host, porta, timeout=60) as s:
        if porta != 465:
            s.starttls()
        if usuario:
            s.login(usuario, os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)


def enviar_telegram(corpo_texto: str) -> None:
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Limite do Telegram: 4096 caracteres por mensagem.
    pedacos, atual = [], ""
    for linha in corpo_texto.splitlines(True):
        if len(atual) + len(linha) > 3900:
            pedacos.append(atual)
            atual = ""
        atual += linha
    if atual:
        pedacos.append(atual)
    for p in pedacos:
        dados = urllib.parse.urlencode({"chat_id": chat, "text": p, "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=dados), timeout=60) as r:
            resp = json.loads(r.read().decode())
            if not resp.get("ok"):
                raise RuntimeError(f"Telegram recusou: {resp.get('description')}")


def enviar(con: sqlite3.Connection, lista: List[Dict], extra_html: str = "",
           canais: Optional[List[str]] = None) -> List[str]:
    """Envia os alertas por todos os canais configurados. Marca como enviados se
    ao menos um canal funcionou. Retorna os canais que funcionaram."""
    canais = canais_configurados() if canais is None else canais
    if not lista:
        return []
    if not canais:
        log.warning("Nenhum canal de alerta configurado: %s alertas ficam pendentes", len(lista))
        return []
    ass, txt = assunto(lista), texto(lista)
    ok = []
    for canal in canais:
        try:
            if canal == "email":
                enviar_email(ass, txt, html(lista, extra_html))
            elif canal == "telegram":
                enviar_telegram(f"{ass}\n\n{txt}")
            ok.append(canal)
        except Exception as e:  # um canal com problema nao derruba os outros
            log.error("Falha ao enviar alerta por %s: %s", canal, e)
    if ok:
        ts = _agora().isoformat()
        with db.transacao(con):
            con.executemany("UPDATE lic_alerta SET enviado_em = ?, canais = ? WHERE chave = ?",
                            [(ts, ",".join(ok), a["chave"]) for a in lista])
    return ok

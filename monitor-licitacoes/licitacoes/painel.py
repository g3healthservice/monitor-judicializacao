"""Painel HTML estatico (GitHub Pages) com os dados PUBLICOS do monitor.

Mostra candidatas, trechos do edital, situacao por item, vencedores e mudancas.
NAO mostra precos indianos, custos, margens, cenarios nem observacoes de
cotacoes.csv: a pagina e publica. Gera um unico arquivo, sem dependencias.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from . import relatorio
from .resultados import FASES
from .sinais import Regras

ROTULO = {"propostas_abertas": "Propostas abertas", "em_julgamento": "Em julgamento",
          "homologado": "Homologado", "fracassado": "Fracassado", "deserto": "Deserto",
          "cancelado": "Cancelado"}

COR_FASE = {
    "propostas_abertas": "info", "em_julgamento": "warn", "homologado": "ok",
    "fracassado": "bad", "deserto": "bad", "cancelado": "muted",
}


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _data(v, hora=False) -> str:
    if not v:
        return "—"
    try:
        d = datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return _e(v)
    return d.strftime("%d/%m/%Y %H:%M" if hora else "%d/%m/%Y")


def _num(v) -> str:
    return "—" if v is None else f"{v:,.0f}".replace(",", ".")


def _brl(v) -> str:
    return "—" if v is None else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ref(c: Dict) -> str:
    return f"PE {c['numero_pregao']}" if c.get("numero_pregao") else f"Contratação {c['identificador']}"


def dados(con: sqlite3.Connection, agora: datetime) -> Dict:
    cands = relatorio.candidatas(con)
    ts = agora.isoformat()
    for c in cands:
        c["itens_det"] = relatorio.itens(con, c["numero_controle"])
        c["aberta"] = bool(c["data_encerramento"] and c["data_encerramento"] > ts)
    desde = (agora - timedelta(days=30)).isoformat()
    ids = {c["numero_controle"] for c in cands}
    eventos = [e for e in relatorio.eventos(con, desde) if e["numero_controle"] in ids]
    fases = dict(con.execute(
        """SELECT i.fase, COUNT(*) FROM lic_item i JOIN lic_compra c USING (numero_controle)
           WHERE c.eh_medicamento = 1 GROUP BY i.fase""").fetchall())
    total_med = con.execute("SELECT COUNT(*) FROM lic_compra WHERE eh_medicamento = 1").fetchone()[0]
    return {"candidatas": cands, "eventos": eventos[::-1], "fases": fases, "total_med": total_med}


def _sinais_html(c: Dict, nomes: Dict[str, str]) -> str:
    vistos, linhas = set(), []
    for s in c["sinais"]:
        if s["sinal_id"] in vistos:
            continue
        vistos.add(s["sinal_id"])
        if s["sequencial_documento"] == 0:
            onde = "objeto"
        elif s["sequencial_documento"] == -1:
            onde = "histórico de resultados"
        else:
            onde = s.get("titulo") or "documento"
        pag = f", p. {s['pagina']}" if s.get("pagina") else ""
        linhas.append(
            f"<li><span class='pill {_e(s['nivel'])}'>{_e(s['nivel'])}</span> "
            f"<b>{_e(nomes.get(s['sinal_id'], s['sinal_id']))}</b> "
            f"<span class='src'>({_e(onde)}{_e(pag)})</span>"
            f"<blockquote>{_e(s['trecho'][:420])}</blockquote></li>")
    return "<ul class='sinais'>" + "".join(linhas) + "</ul>"


def _itens_html(c: Dict) -> str:
    linhas = []
    for i in c["itens_det"]:
        fase = i.get("fase") or ""
        venc = _e(i["vencedor"]) if i.get("vencedor") else "—"
        linhas.append(
            f"<tr><td class='n'>{i['numero_item']}</td><td>{_e(i['descricao'])}</td>"
            f"<td class='n'>{_num(i['quantidade'])}</td><td>{_e(i['unidade'])}</td>"
            f"<td><span class='pill {COR_FASE.get(fase, 'muted')}' title='{_e(FASES.get(fase, ''))}'>"
            f"{_e(ROTULO.get(fase, i.get('situacao') or '—'))}</span></td>"
            f"<td>{venc}</td><td class='n'>{_brl(i.get('valor_unitario_homologado'))}</td></tr>")
    return ("<div class='tbl'><table><thead><tr><th>Item</th><th>Descrição</th><th>Qtd.</th><th>Unid.</th>"
            "<th>Situação</th><th>Vencedor</th><th>R$/un homologado</th></tr></thead><tbody>"
            + "".join(linhas) + "</tbody></table></div>")


def _evento_txt(e: Dict) -> str:
    item = f"item {e['numero_item']} ({(e.get('descricao') or '')[:40]}): " if e.get("numero_item") else ""
    if e["tipo"] == "situacao":
        return f"{item}{e['de']} → {e['para']}"
    if e["tipo"] == "resultado":
        return f"{item}vencedor {e['para']}"
    if e["tipo"] == "referencia":
        return f"{item}referência R$ {e['de']} → R$ {e['para']}"
    return f"{e['para']} ({e['detalhe']})"


def gerar_html(con: sqlite3.Connection, regras: Regras, agora: datetime = None) -> str:
    agora = agora or datetime.now()
    d = dados(con, agora)
    nomes = {s.id: s.nome for s in regras.sinais}
    cands = d["candidatas"]
    abertas = sorted([c for c in cands if c["aberta"]], key=lambda c: c["data_encerramento"])
    em_julg = sum(1 for c in cands for i in c["itens_det"] if i.get("fase") == "em_julgamento")
    sem_suc = d["fases"].get("fracassado", 0) + d["fases"].get("deserto", 0)

    kpis = [
        ("Propostas abertas", len(abertas), "candidatas com prazo em aberto"),
        ("Candidatas", len(cands), f"de {d['total_med']} contratações de medicamento"),
        ("Itens em julgamento", em_julg, "nas candidatas"),
        ("Itens sem sucesso", sem_suc, "fracassados ou desertos (DLOG, 2026)"),
    ]
    kpi_html = "".join(f"<div class='kpi'><div class='kv'>{v}</div><div class='kl'>{_e(t)}</div>"
                       f"<div class='ks'>{_e(s)}</div></div>" for t, v, s in kpis)

    if abertas:
        abertas_html = "<ul class='prazos'>" + "".join(
            f"<li><b>{_data(c['data_encerramento'], True)}</b> — <a href='#c-{_e(c['identificador'])}'>"
            f"{_e(_ref(c))}</a>: {_e(c['objeto'][:140])}</li>" for c in abertas) + "</ul>"
    else:
        abertas_html = "<p class='vazio'>Nenhuma candidata com prazo de proposta em aberto.</p>"

    cards = []
    for c in cands:
        estado = "<span class='pill info'>propostas abertas</span>" if c["aberta"] else \
                 "<span class='pill muted'>propostas encerradas</span>"
        busca = _e(" ".join([_ref(c), c["identificador"], c["objeto"] or ""]
                            + [i["descricao"] or "" for i in c["itens_det"]]).lower())
        cards.append(f"""
<article class='card' id='c-{_e(c['identificador'])}' data-busca='{busca}'>
  <header><h3>{_e(_ref(c))} <span class='sub'>contratação {_e(c['identificador'])}</span></h3>
    <div class='meta'><span class='score'>{c['pontuacao']} pts</span> {estado}
      <span>Propostas até {_data(c['data_encerramento'], True)}</span>
      <a href='{_e(c['url_pncp'])}' target='_blank' rel='noopener'>PNCP ↗</a></div></header>
  <p class='obj'>{_e(c['objeto'])}</p>
  <details><summary>Sinais de produto sem registro ({len({s['sinal_id'] for s in c['sinais']})})</summary>
    {_sinais_html(c, nomes)}</details>
  <details open><summary>Itens ({len(c['itens_det'])})</summary>{_itens_html(c)}</details>
</article>""")

    if d["eventos"]:
        ev_html = "<div class='tbl'><table><thead><tr><th>Detectado em</th><th>Contratação</th><th>Mudança</th></tr></thead><tbody>" + \
            "".join(f"<tr><td class='nw'>{_data(e['detectado_em'], True)}</td><td class='nw'>"
                    f"{_e('PE ' + e['numero_pregao'] if e.get('numero_pregao') else e['identificador'])}</td>"
                    f"<td>{_e(_evento_txt(e))}</td></tr>" for e in d["eventos"][:60]) + "</tbody></table></div>"
    else:
        ev_html = "<p class='vazio'>Nenhuma mudança detectada nos últimos 30 dias.</p>"

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar de Licitações</title>
<meta name="description" content="Licitações federais de medicamentos que admitem produto sem registro na Anvisa (DLOG/MS).">
<style>
:root{{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e3de;--acc:#1f5fa8;
--ok:#1e7a46;--ok-bg:#e3f3e9;--warn:#8a5a00;--warn-bg:#fbf0d9;--bad:#a1262a;--bad-bg:#fbe5e5;
--info:#1f5fa8;--info-bg:#e3edf8;--muted-bg:#eeeeea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--card:#1e1e1e;--fg:#ececea;--mut:#a3a39e;--line:#33332f;
--acc:#7fb0ea;--ok:#7fd3a0;--ok-bg:#1d3326;--warn:#f0c46a;--warn-bg:#3a2f17;--bad:#f2a0a2;--bad-bg:#3d1f20;
--info:#9cc3f0;--info-bg:#1d2b3d;--muted-bg:#2a2a27}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:18px;margin:32px 0 12px}} h3{{font-size:16px;margin:0}}
.lead{{color:var(--mut);margin:0 0 20px}}
a{{color:var(--acc)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.kv{{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}} .kl{{font-weight:600}} .ks{{color:var(--mut);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 12px}}
.card header{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px}}
.sub{{color:var(--mut);font-weight:400;font-size:13px}}
.meta{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:13px;color:var(--mut)}}
.score{{font-weight:700;color:var(--fg)}}
.obj{{margin:8px 0}}
details{{margin-top:8px}} summary{{cursor:pointer;font-weight:600}}
.pill{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;background:var(--muted-bg);color:var(--mut)}}
.pill.ok{{background:var(--ok-bg);color:var(--ok)}} .pill.warn{{background:var(--warn-bg);color:var(--warn)}}
.pill.bad{{background:var(--bad-bg);color:var(--bad)}} .pill.info{{background:var(--info-bg);color:var(--info)}}
.pill.alto{{background:var(--bad-bg);color:var(--bad)}} .pill.medio{{background:var(--warn-bg);color:var(--warn)}}
.pill.baixo{{background:var(--info-bg);color:var(--info)}}
.sinais{{list-style:none;padding:0;margin:8px 0}} .sinais li{{margin:0 0 10px}} .src{{color:var(--mut);font-size:13px}}
blockquote{{margin:4px 0 0;padding:6px 10px;border-left:3px solid var(--line);color:var(--mut);font-size:14px}}
.tbl{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:13px}} td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.nw{{white-space:nowrap}}
.prazos{{padding-left:18px}} .vazio{{color:var(--mut)}}
input[type=search]{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit;margin:0 0 12px}}
footer{{margin-top:40px;color:var(--mut);font-size:13px}}
</style></head>
<body><div class="wrap">
<h1>Radar de Licitações sem registro</h1>
<p class="lead">Pregões da DLOG/Ministério da Saúde (UASG 250005) que admitem medicamento sem registro na Anvisa.
Atualizado em {_data(agora, True)} a partir do PNCP.</p>
<section class="kpis">{kpi_html}</section>
<h2>Prazos de proposta em aberto</h2>{abertas_html}
<h2>Candidatas</h2>
<input type="search" id="busca" placeholder="Filtrar por pregão, medicamento ou palavra do objeto" aria-label="Filtrar candidatas">
<div id="cards">{''.join(cards) or "<p class='vazio'>Nenhuma candidata.</p>"}</div>
<h2>Mudanças nos últimos 30 dias</h2>{ev_html}
<footer>
<p>Fonte: Portal Nacional de Contratações Públicas (PNCP), dados públicos. Uma contratação é candidata quando o
edital soma pelo menos 6 pontos em sinais de produto sem registro, com pelo menos um sinal alto. Cada sinal é uma frase
encontrada no texto e não interpreta o contexto: confira o trecho. O PNCP não informa a fase da sessão
(aceitação, habilitação, diligência): "Em julgamento" quer dizer prazo de propostas encerrado e resultado ainda
não publicado. O PNCP pode atrasar em relação ao Compras.gov.br.</p>
</footer>
</div>
<script>
(function(){{var q=document.getElementById('busca');if(!q)return;
q.addEventListener('input',function(){{var t=q.value.toLowerCase().trim();
document.querySelectorAll('#cards .card').forEach(function(c){{
c.style.display=!t||c.getAttribute('data-busca').indexOf(t)>-1?'':'none';}});}});}})();
</script>
</body></html>
"""


def gravar(con: sqlite3.Connection, regras: Regras, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar_html(con, regras), encoding="utf-8")
    return destino

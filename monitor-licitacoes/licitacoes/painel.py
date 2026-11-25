"""Painel HTML estatico (GitHub Pages) com os dados PUBLICOS do monitor.

Organizado pelo que a pessoa precisa fazer: primeiro o que pede acao (prazos
abertos, itens em julgamento, itens fracassados), depois todas as candidatas com
filtros, as mudancas recentes e um guia de uso.

NAO mostra precos indianos, custos, margens, cenarios nem observacoes de
cotacoes.csv: a pagina e publica. Gera um unico arquivo, sem dependencias.
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from . import relatorio
from .relogio import agora_brasilia
from .sinais import Regras

ROTULO = {"propostas_abertas": "Propostas abertas", "em_julgamento": "Em julgamento",
          "homologado": "Homologado", "fracassado": "Fracassado", "deserto": "Deserto",
          "cancelado": "Cancelado"}
COR = {"propostas_abertas": "info", "em_julgamento": "warn", "homologado": "ok",
       "fracassado": "bad", "deserto": "bad", "cancelado": "muted"}
EXPLICA_FASE = {
    "propostas_abertas": "O pregão ainda recebe propostas.",
    "em_julgamento": "As propostas já foram enviadas e o resultado não saiu. É aqui que acontecem aceitação, habilitação e diligências.",
    "homologado": "Tem vencedor. O valor homologado mostra o preço aceito.",
    "fracassado": "Ninguém foi aceito (preço alto ou documentação). Costuma ser comprado de novo.",
    "deserto": "Ninguém apresentou proposta. Costuma ser comprado de novo.",
    "cancelado": "O item foi anulado, revogado ou cancelado.",
}
# Explicacao em linguagem simples de cada sinal (o nome tecnico fica no detalhe).
SINAL_SIMPLES = {
    "sem_registro_anvisa": "O edital aceita produto sem registro na Anvisa",
    "importacao_excepcional": "Cita importação excepcional (RDC 203/2017 ou 488/2021)",
    "historico_fracasso": "O mesmo medicamento já fracassou ou ficou deserto antes",
    "ich_ou_oms": "Aceita registro em país ICH ou pré-qualificação da OMS",
    "empresa_estrangeira": "Permite a participação de empresa estrangeira",
    "importacao_logistica": "Trata de licença de importação ou desembaraço",
    "demanda_judicial": "Compra para atender ações judiciais",
}


def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def _data(v, hora=False) -> str:
    d = _dt(v)
    if not d:
        return "—"
    return d.strftime("%d/%m/%Y às %H:%M" if hora else "%d/%m/%Y")


def _faltam(v, agora: datetime) -> str:
    d = _dt(v)
    if not d:
        return ""
    horas = (d - agora).total_seconds() / 3600
    if horas < 0:
        return "encerrado"
    if horas < 48:
        return f"faltam {max(1, round(horas))} h"
    return f"faltam {round(horas / 24)} dias"


def _num(v) -> str:
    return "—" if v is None else f"{v:,.0f}".replace(",", ".")


def _brl(v) -> str:
    return "—" if v is None else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ref(c: Dict) -> str:
    return f"Pregão {c['numero_pregao']}" if c.get("numero_pregao") else f"Contratação {c['identificador']}"


def _forca(p: int) -> tuple:
    if p >= 12:
        return "forte", "ok"
    if p >= 8:
        return "bom", "info"
    return "moderado", "warn"


def _situacao(c: Dict) -> Dict:
    fases = [i.get("fase") for i in c["itens_det"]]
    cont = {f: fases.count(f) for f in set(fases)}
    if c["aberta"]:
        principal = "abertas"
    elif cont.get("em_julgamento"):
        principal = "julgamento"
    else:
        principal = "encerrada"
    return {"cont": cont, "principal": principal,
            "fracasso": bool(cont.get("fracassado") or cont.get("deserto"))}


def dados(con: sqlite3.Connection, agora: datetime) -> Dict:
    cands = relatorio.candidatas(con)
    ts = agora.isoformat()
    for c in cands:
        c["itens_det"] = relatorio.itens(con, c["numero_controle"])
        c["aberta"] = bool(c["data_encerramento"] and c["data_encerramento"] > ts)
        c["sit"] = _situacao(c)
    desde = (agora - timedelta(days=30)).isoformat()
    ids = {c["numero_controle"] for c in cands}
    eventos = [e for e in relatorio.eventos(con, desde) if e["numero_controle"] in ids]
    total_med = con.execute("SELECT COUNT(*) FROM lic_compra WHERE eh_medicamento = 1").fetchone()[0]
    return {"candidatas": cands, "eventos": eventos[::-1], "total_med": total_med}


# -- Pedacos de HTML ---------------------------------------------------------

def _pill(texto, cor, titulo="") -> str:
    t = f" title='{_e(titulo)}'" if titulo else ""
    return f"<span class='pill {cor}'{t}>{_e(texto)}</span>"


def _resumo_itens(c: Dict) -> str:
    ordem = ["propostas_abertas", "em_julgamento", "homologado", "fracassado", "deserto", "cancelado"]
    partes = [_pill(f"{c['sit']['cont'][f]} {'item' if c['sit']['cont'][f] == 1 else 'itens'}: "
                    f"{ROTULO[f].lower()}", COR[f], EXPLICA_FASE[f])
              for f in ordem if c["sit"]["cont"].get(f)]
    return " ".join(partes)


def _motivos(c: Dict, nomes: Dict[str, str]) -> str:
    ids, vistos = [], set()
    for s in c["sinais"]:
        if s["sinal_id"] not in vistos:
            vistos.add(s["sinal_id"])
            ids.append(s)
    curtos = "".join(f"<li>{_e(SINAL_SIMPLES.get(s['sinal_id'], nomes.get(s['sinal_id'], s['sinal_id'])))}</li>"
                     for s in ids)
    provas = []
    for s in ids:
        if s["sequencial_documento"] == 0:
            onde = "no objeto da contratação"
        elif s["sequencial_documento"] == -1:
            onde = "no histórico de resultados"
        else:
            onde = f"em {s.get('titulo') or 'documento'}" + (f", página {s['pagina']}" if s.get("pagina") else "")
        provas.append(f"<li><b>{_e(SINAL_SIMPLES.get(s['sinal_id'], s['sinal_id']))}</b> "
                      f"<span class='mut'>({_e(onde)})</span><blockquote>“{_e(s['trecho'][:420])}”</blockquote></li>")
    return (f"<ul class='motivos'>{curtos}</ul>"
            f"<details class='sub'><summary>Ver as frases do edital que comprovam</summary>"
            f"<ul class='provas'>{''.join(provas)}</ul></details>")


def _tabela_itens(c: Dict) -> str:
    linhas = []
    for i in c["itens_det"]:
        f = i.get("fase") or ""
        venc = (f"{_e(i['vencedor'])}<div class='mut'>{_brl(i.get('valor_unitario_homologado'))} por unidade</div>"
                if i.get("vencedor") else "<span class='mut'>—</span>")
        linhas.append(
            f"<tr><td class='n'>{i['numero_item']}</td><td>{_e(i['descricao'])}"
            f"<div class='mut'>{_num(i['quantidade'])} {_e(i['unidade'])}</div></td>"
            f"<td>{_pill(ROTULO.get(f, i.get('situacao') or '—'), COR.get(f, 'muted'), EXPLICA_FASE.get(f, ''))}</td>"
            f"<td>{venc}</td></tr>")
    return ("<div class='tbl'><table><thead><tr><th>Item</th><th>Medicamento e quantidade</th>"
            "<th>Situação</th><th>Vencedor</th></tr></thead><tbody>" + "".join(linhas) + "</tbody></table></div>")


def _card(c: Dict, nomes: Dict[str, str], agora: datetime) -> str:
    forca, cor_forca = _forca(c["pontuacao"])
    sit = c["sit"]
    if c["aberta"]:
        prazo = (f"<div class='prazo aberto'><b>Propostas até {_data(c['data_encerramento'], True)}</b>"
                 f" · {_e(_faltam(c['data_encerramento'], agora))}</div>")
    else:
        prazo = f"<div class='prazo'>Propostas encerradas em {_data(c['data_encerramento'], True)}</div>"
    filtros = [sit["principal"]] + (["fracasso"] if sit["fracasso"] else [])
    busca = " ".join([_ref(c), c["identificador"], c["objeto"] or ""] +
                     [i["descricao"] or "" for i in c["itens_det"]]).lower()
    return f"""
<article class='card' id='c-{_e(c['identificador'])}' data-filtros='{_e(" ".join(filtros))}' data-busca='{_e(busca)}'>
  <div class='card-top'>
    <div><h3>{_e(_ref(c))}</h3><div class='mut'>{_e(('Contratação ' + c['identificador'] + ' · ') if c.get('numero_pregao') else '')}DLOG/Ministério da Saúde</div></div>
    <div class='forca'>{_pill('Indício ' + forca, cor_forca, f'{c["pontuacao"]} pontos nos sinais do edital')}</div>
  </div>
  {prazo}
  <p class='obj'>{_e(c['objeto'])}</p>
  <div class='resumo'>{_resumo_itens(c)}</div>
  <div class='acoes'><a class='btn' href='{_e(c['url_pncp'])}' target='_blank' rel='noopener'>Abrir edital no PNCP ↗</a></div>
  <details><summary>Por que esta compra pode aceitar produto sem registro</summary>{_motivos(c, nomes)}</details>
  <details><summary>{'Ver o item e a sua situação' if len(c['itens_det']) == 1 else f"Ver os {len(c['itens_det'])} itens e a situação de cada um"}</summary>{_tabela_itens(c)}</details>
</article>"""


def _evento_txt(e: Dict) -> str:
    item = f"Item {e['numero_item']} ({(e.get('descricao') or '')[:45]}): " if e.get("numero_item") else ""
    if e["tipo"] == "situacao":
        return f"{item}passou de “{e['de']}” para “{e['para']}”"
    if e["tipo"] == "resultado":
        return f"{item}vencedor definido: {e['para']}"
    if e["tipo"] == "referencia":
        return f"{item}preço de referência mudou de R$ {e['de']} para R$ {e['para']}"
    return f"{e['para']} ({e['detalhe']})"


def gerar_html(con: sqlite3.Connection, regras: Regras, agora: datetime = None) -> str:
    agora = agora or agora_brasilia()
    d = dados(con, agora)
    nomes = {s.id: s.nome for s in regras.sinais}
    cands = d["candidatas"]
    abertas = sorted([c for c in cands if c["aberta"]], key=lambda c: c["data_encerramento"])
    julg = [c for c in cands if c["sit"]["principal"] == "julgamento"]
    fracas = [c for c in cands if c["sit"]["fracasso"]]
    n_julg_itens = sum(c["sit"]["cont"].get("em_julgamento", 0) for c in cands)
    n_frac_itens = sum(c["sit"]["cont"].get("fracassado", 0) + c["sit"]["cont"].get("deserto", 0) for c in cands)

    # 1. Agir agora
    if abertas:
        acao_abertas = "".join(
            f"<li><a href='#c-{_e(c['identificador'])}' data-ir='abertas'><b>{_e(_ref(c))}</b></a> — "
            f"{_e((c['objeto'] or '')[:110])}<div class='mut'>Propostas até {_data(c['data_encerramento'], True)} · "
            f"<b class='urg'>{_e(_faltam(c['data_encerramento'], agora))}</b></div></li>" for c in abertas)
    else:
        acao_abertas = "<li class='mut'>Nenhuma no momento. Quando sair um edital novo, ele aparece aqui e chega por e-mail.</li>"

    def _lista(cs, chave):
        if not cs:
            return "<li class='mut'>Nenhuma no momento.</li>"
        return "".join(f"<li><a href='#c-{_e(c['identificador'])}' data-ir='{chave}'><b>{_e(_ref(c))}</b></a> — "
                       f"{_e((c['objeto'] or '')[:90])}</li>" for c in cs[:6])

    agir = f"""
<section id='agir' class='agir'>
  <div class='bloco'>
    <h3><span class='dot info'></span>Propostas abertas <span class='cont'>{len(abertas)}</span></h3>
    <p class='mut'>Pregões que ainda recebem proposta. É a principal oportunidade: abra o edital e decida se vai participar.</p>
    <ul>{acao_abertas}</ul>
  </div>
  <div class='bloco'>
    <h3><span class='dot warn'></span>Em julgamento <span class='cont'>{n_julg_itens} itens</span></h3>
    <p class='mut'>Propostas já enviadas, resultado ainda não saiu. Acompanhe: um item pode fracassar e ser comprado de novo.</p>
    <ul>{_lista(julg, 'julgamento')}</ul>
  </div>
  <div class='bloco'>
    <h3><span class='dot bad'></span>Itens fracassados ou desertos <span class='cont'>{n_frac_itens} itens</span></h3>
    <p class='mut'>Ninguém foi aceito ou ninguém ofertou. O Ministério costuma comprar de novo: fique atento ao próximo edital.</p>
    <ul>{_lista(fracas, 'fracasso')}</ul>
  </div>
</section>"""

    cards = "".join(_card(c, nomes, agora) for c in
                    sorted(cands, key=lambda c: (not c["aberta"], c["sit"]["principal"] != "julgamento",
                                                 -(c["pontuacao"] or 0))))

    chips = [("todas", "Todas", len(cands)), ("abertas", "Propostas abertas", len(abertas)),
             ("julgamento", "Em julgamento", len(julg)), ("fracasso", "Com item fracassado ou deserto", len(fracas)),
             ("encerrada", "Encerradas", sum(1 for c in cands if c["sit"]["principal"] == "encerrada"))]
    chips_html = "".join(f"<button type='button' class='chip{' on' if k == 'todas' else ''}' data-f='{k}' "
                         f"aria-pressed='{'true' if k == 'todas' else 'false'}'>{_e(t)} <span>{n}</span></button>"
                         for k, t, n in chips)

    if d["eventos"]:
        ev_html = "<ul class='eventos'>" + "".join(
            f"<li><span class='quando'>{_data(e['detectado_em'], True)}</span>"
            f"<a href='#c-{_e(e['identificador'])}'>{_e('Pregão ' + e['numero_pregao'] if e.get('numero_pregao') else e['identificador'])}</a>"
            f" — {_e(_evento_txt(e))}</li>" for e in d["eventos"][:50]) + "</ul>"
    else:
        ev_html = ("<p class='mut'>Nenhuma mudança nos últimos 30 dias. Quando um item mudar de situação "
                   "(por exemplo, de “em julgamento” para “fracassado”), a mudança aparece aqui e chega por e-mail.</p>")

    legenda = "".join(f"<li>{_pill(ROTULO[f], COR[f])} {_e(EXPLICA_FASE[f])}</li>"
                      for f in ["propostas_abertas", "em_julgamento", "homologado", "fracassado", "deserto"])

    proxima = agora.replace(hour=6, minute=17, second=0)
    if proxima <= agora:
        proxima += timedelta(days=1)
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar de Licitações</title>
<meta name="description" content="Licitações federais de medicamentos que admitem produto sem registro na Anvisa (DLOG/MS).">
<style>
:root{{--bg:#f6f6f3;--card:#fff;--fg:#1b1b19;--mut:#66665f;--line:#e3e2dc;--acc:#1f5fa8;--acc-fg:#fff;
--ok:#1e7a46;--ok-bg:#e2f2e8;--warn:#8a5a00;--warn-bg:#fbefd6;--bad:#a1262a;--bad-bg:#fbe4e4;
--info:#1f5fa8;--info-bg:#e2ecf8;--muted-bg:#ecebe6;--shadow:0 1px 2px rgba(0,0,0,.04)}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--card:#1d1d1b;--fg:#ededea;--mut:#a3a39c;--line:#33332f;
--acc:#8ab8ee;--acc-fg:#10233a;--ok:#82d4a3;--ok-bg:#1c3226;--warn:#f0c46a;--warn-bg:#3a2e15;--bad:#f2a2a4;--bad-bg:#3c1f20;
--info:#9cc3f0;--info-bg:#1c2a3c;--muted-bg:#2a2a27;--shadow:none}}}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
a{{color:var(--acc)}} .mut{{color:var(--mut);font-size:13.5px}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 16px 56px}}
nav{{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}}
nav .in{{max-width:1080px;margin:0 auto;padding:10px 16px;display:flex;gap:6px;overflow-x:auto;white-space:nowrap}}
nav a{{text-decoration:none;color:var(--fg);padding:6px 10px;border-radius:8px;font-size:14px}}
nav a:hover{{background:var(--muted-bg)}} nav .marca{{font-weight:700;margin-right:6px}}
header.topo{{padding:24px 0 8px}} h1{{font-size:26px;line-height:1.2;margin:0 0 6px}}
h2{{font-size:19px;margin:36px 0 6px;scroll-margin-top:60px}} h2+.mut{{margin:0 0 14px}}
h3{{font-size:16px;margin:0}}
.atual{{display:inline-flex;gap:8px;align-items:center;font-size:13.5px;color:var(--mut);margin-top:6px}}
.guia-curto{{background:var(--info-bg);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:16px 0 0}}
.guia-curto ol{{margin:6px 0 0;padding-left:20px}} .guia-curto li{{margin:3px 0}}
.agir{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.bloco{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:var(--shadow)}}
.bloco h3{{display:flex;align-items:center;gap:8px}} .bloco p{{margin:6px 0 10px}}
.bloco ul{{margin:0;padding-left:18px}} .bloco li{{margin:0 0 8px}}
.cont{{margin-left:auto;font-size:13px;font-weight:600;color:var(--mut)}}
.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--mut)}}
.dot.info{{background:var(--info)}} .dot.warn{{background:var(--warn)}} .dot.bad{{background:var(--bad)}}
.urg{{color:var(--bad)}}
.filtros{{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 10px}}
.chip{{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--card);color:var(--fg);padding:6px 12px;border-radius:999px;cursor:pointer}}
.chip span{{color:var(--mut);margin-left:4px}} .chip.on{{background:var(--acc);border-color:var(--acc);color:var(--acc-fg)}}
.chip.on span{{color:inherit;opacity:.85}}
input[type=search]{{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);font:inherit;margin:0 0 14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 12px;box-shadow:var(--shadow);scroll-margin-top:64px}}
.card:target{{outline:2px solid var(--acc)}}
.card-top{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}}
.prazo{{margin:10px 0 0;font-size:14px;color:var(--mut)}} .prazo.aberto{{color:var(--info);background:var(--info-bg);padding:6px 10px;border-radius:8px;display:inline-block}}
.obj{{margin:10px 0}} .resumo{{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}}
.acoes{{margin:0 0 6px}}
.btn{{display:inline-block;text-decoration:none;background:var(--acc);color:var(--acc-fg);padding:8px 14px;border-radius:8px;font-weight:600;font-size:14px}}
details{{border-top:1px solid var(--line);margin-top:10px;padding-top:10px}}
details.sub{{border:0;margin:6px 0 0;padding:0}}
summary{{cursor:pointer;font-weight:600;color:var(--acc)}} details.sub summary{{font-weight:500;font-size:14px}}
.motivos{{margin:8px 0 0;padding-left:20px}} .provas{{list-style:none;padding:0;margin:8px 0 0}} .provas li{{margin:0 0 10px}}
blockquote{{margin:4px 0 0;padding:6px 10px;border-left:3px solid var(--line);color:var(--mut);font-size:14px}}
.pill{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12.5px;font-weight:600;background:var(--muted-bg);color:var(--mut);cursor:default}}
.pill.ok{{background:var(--ok-bg);color:var(--ok)}} .pill.warn{{background:var(--warn-bg);color:var(--warn)}}
.pill.bad{{background:var(--bad-bg);color:var(--bad)}} .pill.info{{background:var(--info-bg);color:var(--info)}}
.tbl{{overflow-x:auto;margin-top:8px}} table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:13px}} td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.eventos{{list-style:none;padding:0;margin:0}} .eventos li{{padding:8px 0;border-bottom:1px solid var(--line)}}
.quando{{display:inline-block;min-width:150px;color:var(--mut);font-size:13.5px}}
.vazio{{color:var(--mut);padding:20px;text-align:center;border:1px dashed var(--line);border-radius:12px;display:none}}
.guia{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 18px 14px}}
.guia h3{{margin:16px 0 6px}} .guia ul{{padding-left:20px;margin:6px 0}} .guia li{{margin:4px 0}}
.legenda{{list-style:none;padding:0}} .legenda li{{margin:6px 0}}
footer{{margin-top:36px;color:var(--mut);font-size:13px}}
@media (max-width:600px){{h1{{font-size:22px}} .quando{{display:block}}}}
</style></head>
<body>
<nav><div class="in"><span class="marca">Radar de Licitações</span>
<a href="#agir">O que fazer agora</a><a href="#candidatas">Candidatas</a><a href="#mudancas">Mudanças</a><a href="#guia">Como usar</a></div></nav>
<div class="wrap">
<header class="topo">
<h1>Licitações de medicamentos que aceitam produto sem registro</h1>
<div class="mut">Pregões do Departamento de Logística do Ministério da Saúde (DLOG, UASG 250005), lidos no PNCP.</div>
<div class="atual">Atualizado em {_data(agora, True)} · próxima atualização {_data(proxima, True)}</div>
<div class="guia-curto"><b>Como usar em 1 minuto</b>
<ol><li><b>Comece por “O que fazer agora”</b>: os pregões com propostas abertas são as oportunidades do momento.</li>
<li><b>Clique no nome do pregão</b> para ver por que ele aceita produto sem registro e a situação de cada item.</li>
<li><b>Use “Abrir edital no PNCP”</b> para ler o edital completo e decidir se participa.</li></ol>
<div class="mut" style="margin-top:6px">Explicação completa em <a href="#guia">Como usar este painel</a>.</div></div>
</header>

<h2 id="agir">O que fazer agora</h2>
<p class="mut">Resumo do que pede atenção hoje, do mais urgente para o menos urgente.</p>
{agir}

<h2 id="candidatas">Candidatas ({len(cands)})</h2>
<p class="mut">Compras que, pelo texto do edital, aceitam medicamento sem registro na Anvisa. São {len(cands)} de {d['total_med']} compras de medicamento lidas em 2026. Filtre pela situação ou busque pelo nome do medicamento.</p>
<div class="filtros" role="group" aria-label="Filtrar candidatas">{chips_html}</div>
<input type="search" id="busca" placeholder="Buscar medicamento, número do pregão ou palavra do objeto" aria-label="Buscar candidatas">
<div id="cards">{cards}</div>
<div class="vazio" id="vazio">Nenhuma candidata com esse filtro.</div>

<h2 id="mudancas">Mudanças nos últimos 30 dias</h2>
<p class="mut">O que mudou nas candidatas desde a leitura anterior: itens que fracassaram, foram homologados ou ganharam vencedor.</p>
{ev_html}

<h2 id="guia">Como usar este painel</h2>
<div class="guia">
<h3>O que é este painel</h3>
<p>Todo dia, às 06:17, um robô lê no Portal Nacional de Contratações Públicas (PNCP) os pregões de medicamentos do
Ministério da Saúde, baixa os editais e procura frases que mostram que a compra aceita produto <b>sem registro na Anvisa</b>
(por exemplo, importação excepcional pela RDC 203/2017). As compras que passam nesse filtro são as <b>candidatas</b>.</p>

<h3>Como ler uma candidata</h3>
<ul>
<li><b>Indício forte, bom ou moderado</b>: quanto o edital indica que aceita produto sem registro. Forte quer dizer que o
edital diz isso com todas as letras; moderado quer dizer que há sinais, mas vale ler o edital com cuidado.</li>
<li><b>Prazo</b>: até quando o pregão recebe propostas. Em azul, ainda está aberto.</li>
<li><b>Resumo dos itens</b>: quantos itens estão em cada situação. Passe o mouse sobre cada etiqueta para ver o que significa.</li>
<li><b>Por que esta compra pode aceitar produto sem registro</b>: os motivos em linguagem simples e, logo abaixo, as frases
exatas do edital, com o nome do arquivo e a página, para você conferir.</li>
<li><b>Itens</b>: cada medicamento, a quantidade, a situação e, quando já houver, o vencedor e o preço aceito.</li>
</ul>

<h3>O que significa cada situação</h3>
<ul class="legenda">{legenda}</ul>

<h3>Rotina sugerida</h3>
<ul>
<li><b>Todo dia</b>: leia o e-mail de alertas. Se não chegou e-mail, não houve novidade.</li>
<li><b>Quando houver propostas abertas</b>: abra o edital no PNCP e verifique se você tem fornecedor com registro em país
ICH ou pré-qualificação da OMS.</li>
<li><b>Itens fracassados ou desertos</b>: anote o medicamento. O Ministério costuma abrir um novo pregão, e o robô avisa quando sair.</li>
</ul>

<h3>Limites</h3>
<ul>
<li>O robô encontra frases no texto e não interpreta o contexto. Uma frase como “não será aceito produto sem registro” também
seria marcada. Por isso cada motivo mostra a frase do edital.</li>
<li>O PNCP não informa diligências e pode demorar para atualizar a situação dos itens em relação ao Compras.gov.br.</li>
<li>Esta página é pública e mostra só dados públicos. Preços de fornecedores e margens não aparecem aqui.</li>
</ul>
</div>

<footer><p>Fonte: Portal Nacional de Contratações Públicas (pncp.gov.br), dados públicos. A decisão de participar é sempre sua.</p></footer>
</div>
<script>
(function(){{
  var filtro='todas', q=document.getElementById('busca'), cards=[].slice.call(document.querySelectorAll('#cards .card'));
  var chips=[].slice.call(document.querySelectorAll('.chip')), vazio=document.getElementById('vazio');
  function aplica(){{
    var t=(q.value||'').toLowerCase().trim(), vis=0;
    cards.forEach(function(c){{
      var okF = filtro==='todas' || (' '+c.getAttribute('data-filtros')+' ').indexOf(' '+filtro+' ')>-1;
      var okB = !t || c.getAttribute('data-busca').indexOf(t)>-1;
      c.style.display = okF&&okB ? '' : 'none'; if(okF&&okB) vis++;
    }});
    vazio.style.display = vis ? 'none' : 'block';
  }}
  function escolhe(f){{
    filtro=f; chips.forEach(function(b){{var on=b.getAttribute('data-f')===f; b.classList.toggle('on',on); b.setAttribute('aria-pressed',on);}});
    aplica();
  }}
  chips.forEach(function(b){{ b.addEventListener('click', function(){{ escolhe(b.getAttribute('data-f')); }}); }});
  q.addEventListener('input', aplica);
  // Links de "O que fazer agora" ajustam o filtro e abrem a candidata.
  document.querySelectorAll('a[href^="#c-"]').forEach(function(a){{
    a.addEventListener('click', function(){{
      escolhe('todas'); q.value=''; aplica();
      var alvo=document.querySelector(a.getAttribute('href'));
      if(alvo){{ var d=alvo.querySelectorAll('details'); if(d[1]) d[1].open=true; }}
    }});
  }});
}})();
</script>
</body></html>
"""


def gravar(con: sqlite3.Connection, regras: Regras, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar_html(con, regras), encoding="utf-8")
    return destino

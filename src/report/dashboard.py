"""Gera o dashboard HTML autocontido (padrao Raio-X de Captacao SUS).

- Um unico arquivo HTML com os dados embutidos como JSON (sem back-end).
- Trava de acesso por convite (G3 Access Gate, tool code "jd").
- Somente dados agregados e SEM PII (mesma fronteira LGPD do pipeline).
- A chave do DataJud jamais entra aqui: o site so recebe o resultado ja processado.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..config.settings import Municipio
from ..report.aggregate import _parse_data
from ..store.models import Movimentacao, Processo

# Trava de acesso por convite (G3 Access Gate). Mesmo segredo mestre dos demais
# dashboards G3; tool code "jd" (judicializacao). Token com perm=true abre tudo.
_ACCESS_GATE = (
    '<script>/*G3ACCESSGATE v2 — controle de acesso por convite (token com validade e ferramenta)*/'
    '(function(){var S="g3hs-2026-a7",T="jd";function d(s){s=s.replace(/-/g,"+").replace(/_/g,"/");'
    'while(s.length%4)s+="=";try{return decodeURIComponent(escape(atob(s)))}catch(e){return null}}'
    'function ck(p){var h=0,x=S+p,i;for(i=0;i<x.length;i++){h=((h<<5)-h+x.charCodeAt(i))|0}'
    'return(h>>>0).toString(36)}function tk(){var m=(location.hash+"&"+location.search)'
    '.match(/[?#&]k=([^&]+)/);return m?decodeURIComponent(m[1]):null}function pr(t){if(!t)return null;'
    'var a=t.split(".");if(a.length!==2)return null;if(ck(a[0])!==a[1])return null;var s=d(a[0]);'
    'if(!s)return null;try{return JSON.parse(s)}catch(e){return null}}function bl(ti,ms){'
    'document.documentElement.innerHTML=\'<head><meta charset="utf-8"><meta name="viewport" '
    'content="width=device-width,initial-scale=1"><title>\'+ti+\'</title></head><body '
    'style="margin:0;font-family:Arial,Helvetica,sans-serif;background:#0f2233;color:#e8eef4;'
    'display:flex;min-height:100vh;align-items:center;justify-content:center"><div '
    'style="max-width:460px;text-align:center;padding:34px"><div style="font-size:46px;'
    'margin-bottom:14px">&#128274;</div><h2 style="margin:0 0 10px;color:#fff;font-size:23px">\'+ti+'
    '\'</h2><p style="line-height:1.6;color:#b7c6d4;font-size:15px">\'+ms+\'</p><p '
    'style="margin-top:24px;font-size:14px;color:#9db2c4">G3 Health Service<br>'
    '<a href="mailto:g3.healthservice@proton.me" style="color:#7fb2e5">g3.healthservice@proton.me</a> '
    '&middot; +55 61 99255-7690</p></div></body>\';if(window.stop)window.stop()}'
    'var R="Esta ferramenta &eacute; apresentada mediante convite. Solicite o seu acesso &agrave; '
    'G3 Health Service.",o=pr(tk()),n=Date.now();if(!o){bl("Acesso restrito",R)}else if(o.perm){}'
    'else if(o.t!==T){bl("Acesso restrito",R)}else if(!o.exp||n>o.exp){bl("Acesso encerrado",'
    '"O per&iacute;odo de acesso a esta demonstra&ccedil;&atilde;o foi conclu&iacute;do. '
    'Para retomar o acesso, fale com a G3 Health Service.")}})();</script>'
)


def _municipios_do_banco(session: Session, ibge_map: Optional[dict]) -> List[Municipio]:
    """Deriva a lista de municipios a partir dos processos ja no banco (modo Brasil inteiro)."""
    from ..config.geo import load_ibge_map

    ibge_map = ibge_map if ibge_map is not None else load_ibge_map()
    codigos = session.exec(
        select(Processo.municipio_ibge).where(Processo.municipio_ibge.is_not(None)).distinct()
    ).all()
    muns = []
    for ibge in codigos:
        par = ibge_map.get(str(ibge))
        nome, uf = (par[0], par[1]) if par else (str(ibge), "")
        muns.append(Municipio(nome=nome, codigo_ibge=str(ibge), uf=uf, tribunal="", comarcas=[]))
    return muns


def build_dataset(
    session: Session,
    municipios: Optional[List[Municipio]] = None,
    ibge_map: Optional[dict] = None,
) -> Dict:
    """Monta o payload agregado (sem PII) para o dashboard.

    municipios=None -> agrupa por todos os municipios presentes no banco.
    """
    if not municipios:
        municipios = _municipios_do_banco(session, ibge_map)

    # Tempos de tramitacao (bulk): ultimo movimento por processo.
    movs = session.exec(select(Movimentacao)).all()
    ult_mov: Dict[str, datetime] = {}
    for mv in movs:
        d = _parse_data(mv.data)
        if d is None:
            continue
        cur = ult_mov.get(mv.numero_processo)
        if cur is None or d > cur:
            ult_mov[mv.numero_processo] = d

    def _tramitacao(p: Processo):
        aj = _parse_data(p.data_ajuizamento)
        um = ult_mov.get(p.numero_processo)
        if aj is None or um is None:
            return None
        return max((um - aj).days, 0)

    muns_payload = []
    total_processos = 0
    total_onc = 0
    classe_total: Counter = Counter()
    ano_total: Counter = Counter()
    tempos_all: List[int] = []

    for m in municipios:
        procs: List[Processo] = session.exec(
            select(Processo).where(Processo.municipio_ibge == m.codigo_ibge)
        ).all()
        if not procs:
            continue
        classes = Counter(p.classe for p in procs if p.classe)
        anos = Counter((p.data_ajuizamento or "")[:4] for p in procs if p.data_ajuizamento)
        onc = sum(1 for p in procs if p.oncologico)
        tempos = [t for t in (_tramitacao(p) for p in procs) if t is not None]

        classe_total.update(classes)
        ano_total.update(anos)
        total_processos += len(procs)
        total_onc += onc
        tempos_all.extend(tempos)

        muns_payload.append({
            "mun": m.nome,
            "uf": m.uf,
            "ibge": m.codigo_ibge,
            "n_processos": len(procs),
            "n_oncologicos": onc,
            "tempo_medio_dias": round(mean(tempos)) if tempos else None,
            "classes": classes.most_common(6),
            "processos": [
                {
                    "numero": p.numero_processo,
                    "classe": p.classe,
                    "cid": p.cid,
                    "onc": p.oncologico,
                    "orgao": p.orgao_julgador,
                    "data": (p.data_ajuizamento or "")[:10],
                    "tramitacao": _tramitacao(p),
                }
                for p in sorted(procs, key=lambda x: x.data_ajuizamento or "", reverse=True)
            ],
        })

    muns_payload.sort(key=lambda x: x["n_processos"], reverse=True)
    anos_ord = sorted(a for a in ano_total if a.isdigit())

    return {
        "gerado_em": datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M"),
        "build": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "totais": {
            "n_processos": total_processos,
            "n_municipios": len(muns_payload),
            "n_oncologicos": total_onc,
            "ano_min": anos_ord[0] if anos_ord else None,
            "ano_max": anos_ord[-1] if anos_ord else None,
            "tempo_medio_dias": round(mean(tempos_all)) if tempos_all else None,
        },
        "por_classe": classe_total.most_common(7),
        "por_ano": [[a, ano_total[a]] for a in anos_ord],
        "muns": muns_payload,
    }


def gerar_dashboard(payload: Dict, destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    html = _HTML_TEMPLATE.replace("__DADOS__", json.dumps(payload, ensure_ascii=False))
    html = html.replace("__GATE__", _ACCESS_GATE)
    html = html.replace("__BUILD__", payload["build"])
    destino.write_text(html, encoding="utf-8")
    return destino


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR"><head>
<meta charset="UTF-8">
__GATE__
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Radar de Volume de Judicialização de Saúde · G3</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,Helvetica,sans-serif;background:#d6dde5;color:#1f2933;font-size:13px}
.hdr{background:linear-gradient(90deg,#0b3349,#15506f);color:#fff;padding:18px 26px;
  display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:50;
  box-shadow:0 2px 8px rgba(11,51,73,.25)}
.hdr h1{font-size:23px;font-weight:800;letter-spacing:-.4px}
.hdr .sub{font-size:12px;opacity:.85;margin-top:4px}
.wrap{padding:18px 22px;max-width:1200px;margin:0 auto}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
.card{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card .lbl{font-size:11px;color:#7a8794;text-transform:uppercase;letter-spacing:.4px}
.card .val{font-size:24px;font-weight:800;color:#0b3349;margin-top:4px}
.card.onc .val{color:#8e44ad}
.aviso{background:#fef6e0;border:1px solid #f0d48a;color:#7a5b12;border-radius:8px;
  padding:9px 13px;font-size:11.5px;margin-bottom:14px;line-height:1.5}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
.panel{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.panel h3{font-size:13px;color:#0b3349;margin-bottom:12px}
.bar{display:flex;align-items:center;gap:10px;margin-bottom:7px;font-size:11.5px}
.bar .name{width:150px;color:#465a6b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .track{flex:1;background:#eef1f4;border-radius:5px;height:16px;overflow:hidden}
.bar .fill{height:100%;border-radius:5px;background:#15506f}
.bar .qtd{width:38px;text-align:right;font-weight:700;color:#0b3349}
.yb{display:flex;align-items:flex-end;gap:3px;height:110px}
.yb .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:3px}
.yb .colbar{width:100%;background:#2e86c1;border-radius:3px 3px 0 0}
.yb .yr{font-size:9px;color:#7a8794;transform:rotate(-45deg);white-space:nowrap}
.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.toolbar select,.toolbar input{padding:8px 10px;border:1px solid #c3ccd6;border-radius:6px;font-size:12px;background:#fff}
.toolbar input{flex:1;min-width:200px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;
  box-shadow:0 1px 4px rgba(0,0,0,.08)}
th{background:#0e3d59;color:#fff;padding:10px;font-size:11px;text-align:left;font-weight:600}
td{padding:9px 10px;border-bottom:1px solid #eef1f4;font-size:12px}
tr:hover td{background:#f6f9fc}
.mun-row{cursor:pointer}
.mun-row td:first-child::before{content:"▸ ";color:#9db2c4}
.drill{background:#f0f4f8}
.drill table{box-shadow:none;border-radius:0}
.tag{display:inline-block;background:#8e44ad;color:#fff;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:700}
.foot{margin:22px 0 8px;font-size:11px;color:#7a8794;text-align:center;line-height:1.6}
</style></head>
<body>
<div class="hdr">
  <div><h1>Radar de Volume de Judicialização de Saúde</h1>
    <div class="sub" id="hsub"></div></div>
  <div style="text-align:right;font-size:11px;opacity:.85">G3 Health Service<br><b>build __BUILD__</b></div>
</div>
<div class="wrap">
  <div class="summary" id="summary"></div>
  <div class="aviso">⚖️ <b>Volume</b> a partir de metadados públicos do DataJud/CNJ. O enquadramento no
    <b>Tema 1.234</b> (faixas de ressarcimento / "dinheiro na mesa") depende do custo anual do tratamento,
    que a base pública não fornece — em implementação, mediante fonte de custo.</div>
  <div class="panels">
    <div class="panel"><h3>Classes processuais mais frequentes</h3><div id="classes"></div></div>
    <div class="panel"><h3>Ações por ano de ajuizamento</h3><div class="yb" id="anos"></div></div>
  </div>
  <div class="toolbar">
    <select id="fuf"><option value="">Todas UFs</option></select>
    <input id="busca" placeholder="Buscar município...">
  </div>
  <table><thead><tr>
    <th>Município</th><th>UF</th><th style="text-align:center">Ações</th>
    <th style="text-align:center">Oncológicas*</th><th style="text-align:center">Tempo médio (dias)</th>
  </tr></thead><tbody id="tbody"></tbody></table>
  <div class="foot" id="foot"></div>
</div>
<script>
const D = __DADOS__;
const num = v => (v==null?'—':Number(v).toLocaleString('pt-BR'));

document.getElementById('hsub').innerHTML =
  D.totais.n_municipios+' municípios · '+D.totais.n_processos+' ações · atualizado '+D.gerado_em;

function cards(){
  const t=D.totais;
  const periodo = (t.ano_min&&t.ano_max)?(t.ano_min+'–'+t.ano_max):'—';
  document.getElementById('summary').innerHTML = `
    <div class="card"><div class="lbl">Ações de saúde</div><div class="val">${num(t.n_processos)}</div></div>
    <div class="card"><div class="lbl">Municípios</div><div class="val">${num(t.n_municipios)}</div></div>
    <div class="card onc"><div class="lbl">Oncológicas sinalizadas*</div><div class="val">${num(t.n_oncologicos)}</div></div>
    <div class="card"><div class="lbl">Período · tempo médio</div><div class="val" style="font-size:16px">${periodo}<br><span style="font-size:13px;color:#7a8794">${num(t.tempo_medio_dias)} dias</span></div></div>`;
}
function classes(){
  const arr=D.por_classe||[], max=Math.max(1,...arr.map(x=>x[1]));
  document.getElementById('classes').innerHTML = arr.map(([c,n])=>`
    <div class="bar"><div class="name" title="${c}">${c}</div>
      <div class="track"><div class="fill" style="width:${Math.round(n/max*100)}%"></div></div>
      <div class="qtd">${n}</div></div>`).join('') || '<div style="color:#9db2c4">Sem dados.</div>';
}
function anos(){
  const arr=(D.por_ano||[]).slice(-14), max=Math.max(1,...arr.map(x=>x[1]));
  document.getElementById('anos').innerHTML = arr.map(([a,n])=>`
    <div class="col"><div class="colbar" style="height:${Math.round(n/max*90)}px" title="${a}: ${n}"></div>
      <div class="yr">${a}</div></div>`).join('') || '<div style="color:#9db2c4">Sem dados.</div>';
}
function ufs(){
  const s=document.getElementById('fuf');
  [...new Set(D.muns.map(m=>m.uf))].filter(Boolean).sort().forEach(u=>{
    const o=document.createElement('option');o.value=u;o.textContent=u;s.appendChild(o);});
}
function drill(m){
  return `<td colspan="5"><table><thead><tr>
    <th>Processo</th><th>Classe</th><th>Órgão julgador</th><th>CID</th>
    <th style="text-align:center">Ajuizamento</th><th style="text-align:center">Tramitação (dias)</th>
    </tr></thead><tbody>`+
    m.processos.map(p=>`<tr><td>${p.numero}</td><td>${p.classe||'—'}</td>
      <td>${p.orgao||'—'}</td><td>${p.cid||'—'}${p.onc?' <span class="tag">ONCO</span>':''}</td>
      <td style="text-align:center">${p.data||'—'}</td>
      <td style="text-align:center">${num(p.tramitacao)}</td></tr>`).join('')+
    `</tbody></table></td>`;
}
function render(){
  const uf=document.getElementById('fuf').value, q=document.getElementById('busca').value.toLowerCase();
  const arr=D.muns.filter(m=>(!uf||m.uf===uf)&&(!q||m.mun.toLowerCase().includes(q)));
  const tb=document.getElementById('tbody');tb.innerHTML='';
  if(!arr.length){
    tb.innerHTML='<tr><td colspan="5" style="text-align:center;color:#7a8794;padding:26px">'+
      (D.totais.n_processos?'Nenhum município neste filtro.':
      'Aguardando o primeiro processamento do DataJud. Configure a chave e rode a automação.')+
      '</td></tr>';
  }
  arr.forEach(m=>{
    const tr=document.createElement('tr');tr.className='mun-row';
    tr.innerHTML=`<td><b>${m.mun}</b></td><td>${m.uf}</td>
      <td style="text-align:center">${num(m.n_processos)}</td>
      <td style="text-align:center">${m.n_oncologicos?'<span class="tag">'+m.n_oncologicos+'</span>':'0'}</td>
      <td style="text-align:center">${num(m.tempo_medio_dias)}</td>`;
    let aberto=false, drow=null;
    tr.onclick=()=>{ if(aberto){drow.remove();aberto=false;return;}
      drow=document.createElement('tr');drow.className='drill';drow.innerHTML=drill(m);
      tr.after(drow);aberto=true; };
    tb.appendChild(tr);
  });
  document.getElementById('foot').innerHTML =
    'Volume a partir de metadados processuais públicos (DataJud/CNJ). Sem dados pessoais identificáveis (LGPD).<br>'+
    '*Oncológicas: sinalização heurística por CID/assunto. © G3 Health Service · build __BUILD__';
}
cards();classes();anos();ufs();render();
document.getElementById('fuf').onchange=render;
document.getElementById('busca').oninput=render;
</script>
</body></html>"""

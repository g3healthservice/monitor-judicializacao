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
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..config.constants import (
    FAIXA_ESTADUAL,
    FAIXA_FEDERAL,
    FAIXA_LOCAL,
    FAIXA_ONCOLOGICO,
)
from ..config.settings import Municipio
from ..store.models import Processo

_FAIXA_LABEL = {
    FAIXA_FEDERAL: "Federal — União 100%",
    FAIXA_ESTADUAL: "Estadual — União 65%",
    FAIXA_ONCOLOGICO: "Oncológico — União 80%",
    FAIXA_LOCAL: "Local — sem ressarcimento",
}

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

    muns_payload = []
    total_faixas: Counter = Counter()
    total_ressarcivel = 0.0
    total_processos = 0
    total_enquadraveis = 0

    for m in municipios:
        procs: List[Processo] = session.exec(
            select(Processo).where(Processo.municipio_ibge == m.codigo_ibge)
        ).all()
        if not procs:
            continue
        faixas: Counter = Counter(p.faixa for p in procs)
        valor = sum(p.valor_ressarcivel_estimado or 0.0 for p in procs)
        cids = Counter(p.cid for p in procs if p.cid)
        enquadraveis = sum(1 for p in procs if p.faixa and p.faixa != FAIXA_LOCAL)

        total_faixas.update(faixas)
        total_ressarcivel += valor
        total_processos += len(procs)
        total_enquadraveis += enquadraveis

        muns_payload.append({
            "mun": m.nome,
            "uf": m.uf,
            "ibge": m.codigo_ibge,
            "comarca": (procs[0].comarca if procs else None),
            "n_processos": len(procs),
            "n_enquadraveis": enquadraveis,
            "valor_ressarcivel": round(valor, 2),
            "faixas": dict(faixas),
            "top_cids": cids.most_common(8),
            "processos": [
                {
                    "numero": p.numero_processo,
                    "faixa": p.faixa,
                    "pct": p.percentual_ressarcivel,
                    "custo": p.custo_anual_estimado,
                    "ressarcivel": p.valor_ressarcivel_estimado,
                    "cid": p.cid,
                    "onc": p.oncologico,
                    "justica": p.justica_competente,
                    "data": (p.data_ajuizamento or "")[:10],
                }
                for p in sorted(procs, key=lambda x: x.valor_ressarcivel_estimado or 0, reverse=True)
            ],
        })

    muns_payload.sort(key=lambda x: x["valor_ressarcivel"], reverse=True)

    return {
        "gerado_em": datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M"),
        "build": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "totais": {
            "n_processos": total_processos,
            "n_enquadraveis": total_enquadraveis,
            "n_municipios": len(muns_payload),
            "valor_total_ressarcivel": round(total_ressarcivel, 2),
            "faixas": dict(total_faixas),
        },
        "faixa_labels": _FAIXA_LABEL,
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
<title>Radar de Judicialização de Medicamentos — Tema 1.234 · G3</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,Helvetica,sans-serif;background:#d6dde5;color:#1f2933;font-size:13px}
.hdr{background:linear-gradient(90deg,#0b3349,#15506f);color:#fff;padding:18px 26px;
  display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:50;
  box-shadow:0 2px 8px rgba(11,51,73,.25)}
.hdr h1{font-size:23px;font-weight:800;letter-spacing:-.4px}
.hdr .sub{font-size:12px;opacity:.85;margin-top:4px}
.wrap{padding:18px 22px;max-width:1200px;margin:0 auto}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.card{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card .lbl{font-size:11px;color:#7a8794;text-transform:uppercase;letter-spacing:.4px}
.card .val{font-size:24px;font-weight:800;color:#0b3349;margin-top:4px}
.card.money .val{color:#1e7e34}
.faixabars{background:#fff;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.faixabars h3{font-size:13px;color:#0b3349;margin-bottom:12px}
.bar{display:flex;align-items:center;gap:10px;margin-bottom:8px;font-size:12px}
.bar .name{width:210px;color:#465a6b}
.bar .track{flex:1;background:#eef1f4;border-radius:6px;height:20px;overflow:hidden}
.bar .fill{height:100%;border-radius:6px}
.bar .qtd{width:44px;text-align:right;font-weight:700;color:#0b3349}
.fed{background:#c0392b}.est{background:#e67e22}.onc{background:#8e44ad}.loc{background:#95a5a6}
.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.toolbar select,.toolbar input{padding:8px 10px;border:1px solid #c3ccd6;border-radius:6px;font-size:12px;background:#fff}
.toolbar input{flex:1;min-width:200px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;
  box-shadow:0 1px 4px rgba(0,0,0,.08)}
th{background:#0e3d59;color:#fff;padding:10px;font-size:11px;text-align:left;font-weight:600}
td{padding:9px 10px;border-bottom:1px solid #eef1f4;font-size:12px}
tr:hover td{background:#f6f9fc}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10.5px;font-weight:700;color:#fff}
.mun-row{cursor:pointer}
.drill{background:#f0f4f8}
.drill table{box-shadow:none;border-radius:0}
.money{color:#1e7e34;font-weight:700}
.foot{margin:22px 0 8px;font-size:11px;color:#7a8794;text-align:center;line-height:1.6}
</style></head>
<body>
<div class="hdr">
  <div><h1>Radar de Judicialização de Medicamentos</h1>
    <div class="sub" id="hsub">Enquadramento Tema 1.234/STF · Súmulas Vinculantes 60 e 61</div></div>
  <div style="text-align:right;font-size:11px;opacity:.85">G3 Health Service<br><b>build __BUILD__</b></div>
</div>
<div class="wrap">
  <div class="summary" id="summary"></div>
  <div class="faixabars"><h3>Distribuição por faixa (Tema 1.234)</h3><div id="bars"></div></div>
  <div class="toolbar">
    <select id="fuf"><option value="">Todas UFs</option></select>
    <input id="busca" placeholder="Buscar município...">
  </div>
  <table><thead><tr>
    <th>Município</th><th>UF</th><th style="text-align:center">Processos</th>
    <th style="text-align:center">Enquadráveis</th><th style="text-align:right">Dinheiro na mesa</th>
  </tr></thead><tbody id="tbody"></tbody></table>
  <div class="foot" id="foot"></div>
</div>
<script>
const D = __DADOS__;
const LBL = D.faixa_labels;
const CLS = {FEDERAL_100:'fed',ESTADUAL_65:'est',ONCOLOGICO_80:'onc',LOCAL_SEM_RESSARCIMENTO:'loc'};
const brl = v => 'R$ '+(v||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
const pct = v => Math.round((v||0)*100)+'%';

document.getElementById('hsub').innerHTML =
  D.totais.n_municipios+' municípios · '+D.totais.n_processos+' processos · atualizado '+D.gerado_em;

function cards(){
  const t=D.totais;
  document.getElementById('summary').innerHTML = `
    <div class="card"><div class="lbl">Municípios monitorados</div><div class="val">${t.n_municipios}</div></div>
    <div class="card"><div class="lbl">Processos de saúde</div><div class="val">${t.n_processos}</div></div>
    <div class="card"><div class="lbl">Ações enquadráveis</div><div class="val">${t.n_enquadraveis}</div></div>
    <div class="card money"><div class="lbl">Dinheiro na mesa (União)</div><div class="val">${brl(t.valor_total_ressarcivel)}</div></div>`;
}
function bars(){
  const f=D.totais.faixas, max=Math.max(1,...Object.values(f));
  const ordem=['FEDERAL_100','ONCOLOGICO_80','ESTADUAL_65','LOCAL_SEM_RESSARCIMENTO'];
  document.getElementById('bars').innerHTML = ordem.map(k=>{
    const q=f[k]||0, w=Math.round(q/max*100);
    return `<div class="bar"><div class="name">${LBL[k]}</div>
      <div class="track"><div class="fill ${CLS[k]}" style="width:${w}%"></div></div>
      <div class="qtd">${q}</div></div>`;
  }).join('');
}
function ufs(){
  const s=document.getElementById('fuf');
  [...new Set(D.muns.map(m=>m.uf))].sort().forEach(u=>{
    const o=document.createElement('option');o.value=u;o.textContent=u;s.appendChild(o);});
}
function drill(m){
  return `<tr class="drill"><td colspan="5"><table><thead><tr>
    <th>Processo</th><th>Faixa</th><th style="text-align:center">% União</th>
    <th style="text-align:right">Custo anual est.</th><th style="text-align:right">Ressarcível</th>
    <th>CID</th><th>Ajuizamento</th></tr></thead><tbody>`+
    m.processos.map(p=>`<tr><td>${p.numero}</td>
      <td><span class="pill ${CLS[p.faixa]}">${LBL[p.faixa]||p.faixa}</span></td>
      <td style="text-align:center">${pct(p.pct)}</td>
      <td style="text-align:right">${p.custo!=null?brl(p.custo):'—'}</td>
      <td style="text-align:right" class="money">${p.ressarcivel!=null?brl(p.ressarcivel):'—'}</td>
      <td>${p.cid||'—'}${p.onc?' 🎗️':''}</td><td>${p.data||'—'}</td></tr>`).join('')+
    `</tbody></table></td></tr>`;
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
      <td style="text-align:center">${m.n_processos}</td>
      <td style="text-align:center">${m.n_enquadraveis}</td>
      <td style="text-align:right" class="money">${brl(m.valor_ressarcivel)}</td>`;
    let aberto=false, drow=null;
    tr.onclick=()=>{ if(aberto){drow.remove();aberto=false;return;}
      drow=document.createElement('tr');drow.innerHTML=drill(m).replace(/^<tr class="drill">|<\/tr>$/g,'');
      drow.className='drill';drow.innerHTML=drill(m);
      tr.after(drow);aberto=true; };
    tb.appendChild(tr);
  });
  document.getElementById('foot').innerHTML =
    'Estimativas a partir de metadados processuais públicos (DataJud/CNJ). Sem dados pessoais identificáveis (LGPD).<br>'+
    'Faixas do Tema 1.234 derivadas do salário mínimo vigente. © G3 Health Service · build __BUILD__';
}
cards();bars();ufs();render();
document.getElementById('fuf').onchange=render;
document.getElementById('busca').oninput=render;
</script>
</body></html>"""

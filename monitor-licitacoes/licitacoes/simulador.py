"""Etapa 4a: simulador de cenarios por item cotado.

Para cada item com preco indiano em cotacoes.csv, calcula custo desembarcado,
margem e o FOB maximo que cabe na referencia em cada cenario de premissas.yaml,
e marca os cenarios em que o custo fica acima da referencia.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .viabilidade import Premissas, _brl

CENARIOS_PADRAO = {"base": {"nome": "Base"}}


def carregar_cenarios(caminho: Path, base: Premissas) -> Dict[str, Dict]:
    d = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    saida = {}
    for chave, c in (d.get("cenarios") or CENARIOS_PADRAO).items():
        p = replace(base)
        p.cambio = base.cambio * float(c.get("cambio_fator", 1.0))
        p.frete = float(c.get("frete_seguro", base.frete))
        p.imposto = float(c.get("imposto_importacao", base.imposto))
        p.desembaraco = float(c.get("desembaraco", base.desembaraco))
        p.desconto = float(c.get("desconto_volume", base.desconto))
        saida[chave] = {"nome": c.get("nome", chave), "premissas": p}
    return saida


def simular(con: sqlite3.Connection, cenarios: Dict[str, Dict], cotacoes: Dict[tuple, Dict],
            numero_controle: Optional[str] = None, item: Optional[int] = None) -> List[Dict]:
    """Uma linha por item cotado com preco indiano, com o resultado de cada cenario."""
    saida = []
    for (ident, n), cot in sorted(cotacoes.items()):
        if not cot.get("preco_inr"):
            continue
        c = con.execute("SELECT * FROM lic_compra WHERE identificador = ?", (ident,)).fetchone()
        if numero_controle and (not c or c["numero_controle"] != numero_controle):
            continue
        if item is not None and n != item:
            continue
        it = con.execute("SELECT * FROM lic_item WHERE numero_controle = ? AND numero_item = ?",
                         (c["numero_controle"], n)).fetchone() if c else None
        ref = cot.get("referencia_brl")
        quantidade = (it["quantidade"] if it else None) or 0
        linhas = []
        for chave, cen in cenarios.items():
            p = cen["premissas"]
            custo = p.custo(cot["preco_inr"])
            margem = (ref - custo) / ref if ref else None
            linhas.append({
                "cenario": chave, "nome": cen["nome"], "custo": custo, "margem": margem,
                "fob_maximo_inr": p.fob_maximo_inr(ref) if ref else None,
                "total": custo * quantidade if quantidade else None,
                "acima_da_referencia": bool(ref) and custo > ref,
            })
        saida.append({"identificador": ident, "numero_pregao": c["numero_pregao"] if c else None,
                      "item": n, "medicamento": cot.get("medicamento") or (it["descricao"] if it else ""),
                      "quantidade": quantidade, "preco_inr": cot["preco_inr"], "referencia": ref,
                      "referencia_tipo": cot.get("referencia_tipo") or "", "cenarios": linhas})
    return saida


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.0%}"


def _inr(v) -> str:
    return "—" if v is None else f"₹ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto(lista: List[Dict]) -> str:
    if not lista:
        return "Nenhum item com preço indiano em cotacoes.csv."
    partes = []
    for s in lista:
        pregao = f"PE {s['numero_pregao']}" if s.get("numero_pregao") else s["identificador"]
        partes.append(f"{pregao} item {s['item']} — {s['medicamento']} | preço indiano {_inr(s['preco_inr'])} | "
                      f"referência ({s['referencia_tipo'] or '—'}) {_brl(s['referencia'])}")
        partes.append(f"   {'Cenário':<28} {'Custo/un':>12} {'Margem':>8} {'FOB máx':>14}")
        for c in s["cenarios"]:
            marca = "  ACIMA DA REFERÊNCIA" if c["acima_da_referencia"] else ""
            partes.append(f"   {c['nome']:<28} {_brl(c['custo']):>12} {_pct(c['margem']):>8} "
                          f"{_inr(c['fob_maximo_inr']):>14}{marca}")
        partes.append("")
    return "\n".join(partes)


def html(lista: List[Dict]) -> str:
    """Tabela para o e-mail, com os cenarios acima da referencia em vermelho."""
    if not lista:
        return ""
    blocos = []
    for s in lista:
        pregao = f"PE {s['numero_pregao']}" if s.get("numero_pregao") else s["identificador"]
        linhas = "".join(
            f"<tr style='color:{'#b00020' if c['acima_da_referencia'] else 'inherit'}'>"
            f"<td>{c['nome']}</td><td align='right'>{_brl(c['custo'])}</td>"
            f"<td align='right'>{_pct(c['margem'])}</td><td align='right'>{_inr(c['fob_maximo_inr'])}</td></tr>"
            for c in s["cenarios"])
        blocos.append(
            f"<p><b>{pregao} item {s['item']}</b> — {s['medicamento']}<br>"
            f"Preço indiano {_inr(s['preco_inr'])} · referência ({s['referencia_tipo'] or '—'}) {_brl(s['referencia'])}</p>"
            "<table cellpadding='4' style='border-collapse:collapse;font-size:13px'>"
            "<tr><th align='left'>Cenário</th><th>Custo/un</th><th>Margem</th><th>FOB máx</th></tr>"
            f"{linhas}</table>")
    return "".join(blocos)


def gravar_csv(lista: List[Dict], pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / "licitacoes_cenarios.csv"
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["contratacao", "pregao", "item", "medicamento", "quantidade", "preco_inr", "referencia_brl",
                    "referencia_tipo", "cenario", "custo_brl", "margem", "fob_maximo_inr", "total_brl",
                    "acima_da_referencia"])
        for s in lista:
            for c in s["cenarios"]:
                w.writerow([s["identificador"], s.get("numero_pregao") or "", s["item"], s["medicamento"],
                            s["quantidade"], s["preco_inr"], s["referencia"] or "", s["referencia_tipo"],
                            c["nome"], round(c["custo"], 4),
                            "" if c["margem"] is None else round(c["margem"], 4),
                            "" if c["fob_maximo_inr"] is None else round(c["fob_maximo_inr"], 2),
                            "" if c["total"] is None else round(c["total"], 2),
                            "sim" if c["acima_da_referencia"] else "não"])
    return caminho

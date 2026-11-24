"""Lista de demandas candidatas, com fonte, data e a frase que gerou cada sinal."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .sinais import Regras


def candidatas(con: sqlite3.Connection, todas: bool = False,
               somente_abertas: bool = False) -> List[Dict]:
    where = ["eh_medicamento = 1"]
    if not todas:
        where.append("candidata = 1")
    if somente_abertas:
        where.append("(data_encerramento IS NULL OR data_encerramento >= datetime('now'))")
    linhas = con.execute(
        f"""SELECT * FROM lic_compra WHERE {' AND '.join(where)}
            ORDER BY pontuacao DESC, data_encerramento DESC"""
    ).fetchall()
    saida = []
    for c in linhas:
        d = dict(c)
        d["sinais"] = [dict(s) for s in con.execute(
            """SELECT s.*, a.titulo FROM lic_sinal s
               LEFT JOIN lic_arquivo a ON a.numero_controle = s.numero_controle
                    AND a.sequencial_documento = s.sequencial_documento
               WHERE s.numero_controle = ? ORDER BY s.peso DESC, s.sinal_id, s.sequencial_documento""",
            (c["numero_controle"],))]
        d["itens"] = [dict(i) for i in con.execute(
            "SELECT numero_item, descricao, quantidade, unidade FROM lic_item WHERE numero_controle = ? ORDER BY numero_item",
            (c["numero_controle"],))]
        saida.append(d)
    return saida


def buscar(con: sqlite3.Connection, numero: str) -> List[Dict]:
    """Encontra contratacoes pelo numero da compra (709), do pregao (90114) ou controle PNCP."""
    numero = numero.strip()
    base = numero.split("/")[0]
    linhas = con.execute(
        """SELECT numero_controle FROM lic_compra
           WHERE numero_compra = ? OR numero_pregao LIKE ? OR numero_controle = ? OR identificador = ?""",
        (base, f"{base}/%", numero, numero)).fetchall()
    ids = {r["numero_controle"] for r in linhas}
    return [c for c in candidatas(con, todas=True) if c["numero_controle"] in ids]


def _sinais_unicos(c: Dict) -> List[Dict]:
    vistos, saida = set(), []
    for s in c["sinais"]:
        if s["sinal_id"] not in vistos:
            vistos.add(s["sinal_id"])
            saida.append(s)
    return saida


def texto_terminal(lista: List[Dict], regras: Regras) -> str:
    nomes = {s.id: s.nome for s in regras.sinais}
    if not lista:
        return "Nenhuma demanda candidata encontrada."
    partes = []
    for c in lista:
        pregao = f"PE {c['numero_pregao']}" if c.get("numero_pregao") else "pregao n/d"
        partes.append(
            f"[{c['pontuacao']:>2} pts{' *' if c['candidata'] else ''}] {pregao} | contratacao "
            f"{c['identificador']} | {c['situacao']} | encerra {(c['data_encerramento'] or '')[:10]}\n"
            f"    {c['objeto'][:180]}\n"
            f"    PNCP: {c['url_pncp']}  (lido em {c['ultima_leitura'][:10]})"
        )
        for s in _sinais_unicos(c):
            onde = "objeto" if s["sequencial_documento"] == 0 else (s.get("titulo") or f"doc {s['sequencial_documento']}")
            pag = f", p. {s['pagina']}" if s.get("pagina") else ""
            partes.append(
                f"      - ({s['nivel']}) {nomes.get(s['sinal_id'], s['sinal_id'])} [{onde}{pag}]\n"
                f"        \"{s['trecho'][:400]}\""
            )
        partes.append("")
    return "\n".join(partes)


def gravar(lista: List[Dict], regras: Regras, pasta: Path) -> Dict[str, Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    nomes = {s.id: s.nome for s in regras.sinais}
    csv_path = pasta / "licitacoes_candidatas.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pontuacao", "candidata", "pregao", "contratacao", "uasg", "situacao",
                    "abertura", "encerramento", "objeto", "itens", "sinal", "nivel", "documento",
                    "pagina", "trecho", "fonte_url", "lido_em", "url_pncp"])
        for c in lista:
            itens = " | ".join(f"{i['numero_item']}: {i['descricao']} ({i['quantidade']:g} {i['unidade']})"
                               for i in c["itens"])
            for s in _sinais_unicos(c) or [{}]:
                w.writerow([
                    c["pontuacao"], c["candidata"], c.get("numero_pregao") or "", c["identificador"],
                    c["uasg"], c["situacao"], (c["data_abertura"] or "")[:16],
                    (c["data_encerramento"] or "")[:16], c["objeto"], itens,
                    nomes.get(s.get("sinal_id"), s.get("sinal_id") or ""), s.get("nivel", ""),
                    "objeto" if s.get("sequencial_documento") == 0 else (s.get("titulo") or ""),
                    s.get("pagina") or "", s.get("trecho", ""), s.get("fonte_url", ""),
                    s.get("lido_em", ""), c["url_pncp"],
                ])
    md_path = pasta / "licitacoes_candidatas.md"
    md_path.write_text(markdown(lista, regras), encoding="utf-8")
    return {"csv": csv_path, "md": md_path}


def markdown(lista: List[Dict], regras: Regras) -> str:
    nomes = {s.id: s.nome for s in regras.sinais}
    linhas = [f"# Demandas candidatas ({len(lista)})", "",
              "| Pontos | Pregão | Contratação | Encerra | Objeto |", "|---|---|---|---|---|"]
    for c in lista:
        linhas.append(
            f"| {c['pontuacao']} | {c.get('numero_pregao') or '—'} | [{c['identificador']}]({c['url_pncp']}) | "
            f"{(c['data_encerramento'] or '')[:10]} | {c['objeto'][:120].replace('|', '/')} |")
    for c in lista:
        linhas += ["", f"## {c.get('numero_pregao') or c['identificador']} — {c['pontuacao']} pontos", "",
                   c["objeto"], "", f"Fonte: {c['url_pncp']} (lido em {c['ultima_leitura'][:10]})", ""]
        for s in _sinais_unicos(c):
            onde = "objeto" if s["sequencial_documento"] == 0 else (s.get("titulo") or "")
            pag = f", p. {s['pagina']}" if s.get("pagina") else ""
            linhas.append(f"- **{nomes.get(s['sinal_id'], s['sinal_id'])}** ({s['nivel']}, {onde}{pag}): "
                          f"“{s['trecho'][:400]}”")
    return "\n".join(linhas) + "\n"


def reavaliar(con: sqlite3.Connection, regras: Regras) -> int:
    """Recalcula pontuacao e candidata a partir dos sinais ja gravados (sem rede)."""
    n = 0
    for c in con.execute("SELECT numero_controle, eh_medicamento FROM lic_compra").fetchall():
        ids = [r["sinal_id"] for r in con.execute(
            "SELECT DISTINCT sinal_id FROM lic_sinal WHERE numero_controle = ?", (c["numero_controle"],))]
        nota = regras.pontuar(ids) if c["eh_medicamento"] else {"pontuacao": 0, "tem_alto": False, "candidata": False}
        con.execute("UPDATE lic_compra SET pontuacao=?, tem_alto=?, candidata=? WHERE numero_controle=?",
                    (nota["pontuacao"], int(nota["tem_alto"]), int(nota["candidata"]), c["numero_controle"]))
        n += 1
    con.commit()
    return n

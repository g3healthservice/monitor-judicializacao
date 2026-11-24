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
            onde = _onde(s)
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
                    _onde(s) if s else "",
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
            onde = _onde(s)
            pag = f", p. {s['pagina']}" if s.get("pagina") else ""
            linhas.append(f"- **{nomes.get(s['sinal_id'], s['sinal_id'])}** ({s['nivel']}, {onde}{pag}): "
                          f"“{s['trecho'][:400]}”")
    return "\n".join(linhas) + "\n"


def reavaliar(con: sqlite3.Connection, regras: Regras) -> int:
    """Recalcula historico, pontuacao e candidata a partir do que ja esta gravado (sem rede)."""
    from .coleta import Coletor
    from .config import Config
    Coletor(Config(), regras, con, cliente=object()).recalcular_historico()
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


# -- Etapa 2 ---------------------------------------------------------------

def _onde(s: Dict) -> str:
    if s["sequencial_documento"] == 0:
        return "objeto"
    if s["sequencial_documento"] == -1:
        return "histórico de resultados"
    return s.get("titulo") or f"doc {s['sequencial_documento']}"


def itens(con: sqlite3.Connection, nc: str) -> List[Dict]:
    """Itens com fase, vencedor (1o classificado), valor homologado e ata vigente."""
    linhas = con.execute(
        """SELECT i.numero_item, i.descricao, i.principio, i.quantidade, i.unidade, i.situacao,
                  i.fase, i.valor_unitario_estimado, i.orcamento_sigiloso,
                  r.fornecedor_nome AS vencedor, r.fornecedor_ni, r.codigo_pais,
                  r.valor_unitario_homologado, r.quantidade_homologada, r.data_resultado,
                  r.data_cancelamento
           FROM lic_item i
           LEFT JOIN lic_resultado r ON r.numero_controle = i.numero_controle
                AND r.numero_item = i.numero_item
                AND r.sequencial_resultado = (SELECT MIN(sequencial_resultado) FROM lic_resultado x
                     WHERE x.numero_controle = i.numero_controle AND x.numero_item = i.numero_item
                       AND x.data_cancelamento IS NULL)
           WHERE i.numero_controle = ? ORDER BY i.numero_item""", (nc,)).fetchall()
    return [dict(r) for r in linhas]


def atas(con: sqlite3.Connection, nc: str) -> List[Dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM lic_ata WHERE numero_controle = ? ORDER BY numero_ata", (nc,))]


def eventos(con: sqlite3.Connection, desde: Optional[str] = None,
            somente_candidatas: bool = False) -> List[Dict]:
    where, params = ["1=1"], []
    if desde:
        where.append("e.detectado_em >= ?")
        params.append(desde)
    if somente_candidatas:
        where.append("c.candidata = 1")
    return [dict(r) for r in con.execute(
        f"""SELECT e.*, c.identificador, c.numero_pregao, c.url_pncp, i.descricao
            FROM lic_evento e JOIN lic_compra c USING (numero_controle)
            LEFT JOIN lic_item i ON i.numero_controle = e.numero_controle AND i.numero_item = e.numero_item
            WHERE {' AND '.join(where)} ORDER BY e.detectado_em, e.id""", params)]


def _milhar(v) -> str:
    return f"{v or 0:,.0f}".replace(",", ".")


def _moeda(v) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto_resultados(con: sqlite3.Connection, lista: List[Dict]) -> str:
    from .resultados import FASES
    if not lista:
        return "Nenhuma contratação encontrada."
    partes = []
    for c in lista:
        pregao = f"PE {c['numero_pregao']}" if c.get("numero_pregao") else c["identificador"]
        partes.append(f"{pregao} | contratação {c['identificador']} | {c['pontuacao']} pts | {c['url_pncp']}")
        for i in itens(con, c["numero_controle"]):
            venc = ""
            if i["vencedor"]:
                venc = f" | {i['vencedor'][:45]} {_moeda(i['valor_unitario_homologado'])}/un"
            partes.append(f"   {i['numero_item']:>3}. {i['descricao'][:48]:<48} {_milhar(i['quantidade']):>10} "
                          f"{(i['unidade'] or '')[:12]:<12} {FASES.get(i['fase'], i['situacao'] or '?')}{venc}")
        for a in atas(con, c["numero_controle"]):
            partes.append(f"   Ata {a['numero_ata']}/{a['ano_ata']}: vigência {a['vigencia_inicio']} a "
                          f"{a['vigencia_fim']}{' (cancelada)' if a['cancelado'] else ''}")
        partes.append("")
    return "\n".join(partes)


def texto_eventos(lista: List[Dict]) -> str:
    if not lista:
        return "Nenhuma mudança detectada no período."
    linhas = []
    for e in lista:
        pregao = f"PE {e['numero_pregao']}" if e.get("numero_pregao") else e["identificador"]
        item = f" item {e['numero_item']} ({(e.get('descricao') or '')[:40]})" if e.get("numero_item") else ""
        if e["tipo"] == "situacao":
            o = f"{e['de']} → {e['para']}"
        elif e["tipo"] == "resultado":
            o = f"vencedor {e['para']} ({e['detalhe']})"
        else:
            o = f"{e['para']} ({e['detalhe']})"
        linhas.append(f"{e['detectado_em'][:16]} | {pregao}{item}: {o}")
    return "\n".join(linhas)


def gravar_itens_csv(con: sqlite3.Connection, lista: List[Dict], pasta: Path) -> Path:
    from .resultados import FASES
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / "licitacoes_itens.csv"
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pregao", "contratacao", "pontuacao", "item", "descricao", "principio", "quantidade",
                    "unidade", "fase", "situacao_pncp", "vencedor", "cnpj_vencedor", "pais",
                    "valor_unitario_homologado", "quantidade_homologada", "data_resultado", "url_pncp"])
        for c in lista:
            for i in itens(con, c["numero_controle"]):
                w.writerow([c.get("numero_pregao") or "", c["identificador"], c["pontuacao"], i["numero_item"],
                            i["descricao"], i["principio"], i["quantidade"], i["unidade"],
                            FASES.get(i["fase"], ""), i["situacao"], i["vencedor"] or "",
                            i["fornecedor_ni"] or "", i["codigo_pais"] or "",
                            i["valor_unitario_homologado"] if i["valor_unitario_homologado"] is not None else "",
                            i["quantidade_homologada"] or "", i["data_resultado"] or "", c["url_pncp"]])
    return caminho

"""Etapa 3: ficha por item e nota de viabilidade de 0 a 100.

Custo desembarcado (mesma formula da planilha de comparativo):

    custo  = FOB * cambio * (1 + frete) * (1 + II + desembaraco) * (1 - desconto)
    FOBmax = referencia / (cambio * (1 + frete) * (1 + II + desembaraco) * (1 - desconto))

A nota soma cinco criterios com pesos ajustaveis (premissas.yaml). Criterio sem
dado conta como neutro (50); a cobertura diz quanto do peso total foi de fato
avaliado com dado. A decisao continua
sendo da pessoa: a nota so ordena e explica.
"""
from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .relogio import agora_brasilia
from .resultados import FASES

PASTA = Path(__file__).resolve().parent
PREMISSAS_PADRAO = PASTA / "premissas.yaml"
COTACOES_PADRAO = PASTA.parent / "cotacoes.csv"

NEUTRO = 0.5

NOMES_CRITERIOS = {
    "admissao": "Admissão de produto sem registro",
    "margem": "Margem estimada",
    "fornecedor": "Disponibilidade de fornecedor",
    "prazo": "Prazo",
    "escala": "Escala",
}


# -- Premissas e custo -----------------------------------------------------

@dataclass
class Premissas:
    cambio: float = 0.055
    frete: float = 0.06
    imposto: float = 0.0
    desembaraco: float = 0.02
    desconto: float = 0.0
    pesos: Dict[str, float] = field(default_factory=lambda: {
        "admissao": 30, "margem": 30, "fornecedor": 15, "prazo": 15, "escala": 10})
    margem_zero: float = -0.10
    margem_cheia: float = 0.30
    prazo_cheio_dias: float = 15

    def custo(self, preco_inr: float) -> float:
        return (preco_inr * self.cambio * (1 + self.frete)
                * (1 + self.imposto + self.desembaraco) * (1 - self.desconto))

    def fob_maximo_inr(self, referencia_brl: float) -> float:
        """Preco de lista indiano que ainda cabe na referencia (inverso exato de custo())."""
        return referencia_brl / (self.cambio * (1 + self.frete) * (1 + self.imposto + self.desembaraco)
                                 * (1 - self.desconto))


def carregar_premissas(caminho: Path = PREMISSAS_PADRAO) -> Premissas:
    d = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    c, v = d.get("custo", {}), d.get("viabilidade", {})
    p = Premissas()
    p.cambio = float(c.get("cambio_brl_por_inr", p.cambio))
    p.frete = float(c.get("frete_seguro", p.frete))
    p.imposto = float(c.get("imposto_importacao", p.imposto))
    p.desembaraco = float(c.get("desembaraco", p.desembaraco))
    p.desconto = float(c.get("desconto_volume", p.desconto))
    p.pesos = {k: float(x) for k, x in (v.get("pesos") or p.pesos).items()}
    p.margem_zero = float(v.get("margem_zero", p.margem_zero))
    p.margem_cheia = float(v.get("margem_cheia", p.margem_cheia))
    p.prazo_cheio_dias = float(v.get("prazo_cheio_dias", p.prazo_cheio_dias))
    return p


# -- Cotacoes registradas pela pessoa --------------------------------------

def _num(v: Optional[str]) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    return float(str(v).strip().replace(",", "."))


def carregar_cotacoes(caminho: Path = COTACOES_PADRAO) -> Dict[tuple, Dict]:
    """{(identificador da contratacao, item): linha}. Numeros com ponto ou virgula."""
    if not Path(caminho).exists():
        return {}
    saida = {}
    with Path(caminho).open(encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            if not (linha.get("contratacao") or "").strip():
                continue
            chave = (linha["contratacao"].strip(), int(linha["item"]))
            for campo in ("preco_inr", "referencia_brl", "menor_oferta_brl",
                          "fabricantes_ich_oms", "capacidade_fornecedor"):
                linha[campo] = _num(linha.get(campo))
            saida[chave] = linha
    return saida


# -- Nota ------------------------------------------------------------------

def _linear(x: float, zero: float, cheio: float) -> float:
    if cheio == zero:
        return 1.0 if x >= cheio else 0.0
    return max(0.0, min(1.0, (x - zero) / (cheio - zero)))


def _admissao(sinais: set) -> tuple:
    if {"sem_registro_anvisa", "importacao_excepcional"} & sinais:
        provas = "pré-qualificação OMS ou registro ICH" if "ich_ou_oms" in sinais else "provas não identificadas"
        return 1.0, f"edital admite produto sem registro ({provas})"
    if "ich_ou_oms" in sinais:
        return 0.6, "edital cita ICH/OMS, sem cláusula expressa de produto sem registro"
    if "historico_fracasso" in sinais:
        return 0.4, "sem cláusula no edital; o mesmo princípio ativo já fracassou antes"
    return 0.0, "edital não indica admissão de produto sem registro"


def _prazo(fase: str, encerramento: Optional[str], prazo_manual: Optional[str],
           agora: datetime, cheio: float) -> tuple:
    alvo, origem = None, ""
    if prazo_manual:
        alvo, origem = prazo_manual, "prazo informado (diligência/proposta)"
    elif fase == "propostas_abertas" and encerramento:
        alvo, origem = encerramento, "fim das propostas"
    if not alvo:
        if fase in ("homologado", "fracassado", "deserto", "cancelado", "em_julgamento"):
            return 0.0, f"sem prazo aberto ({FASES.get(fase, fase).lower()})"
        return None, "prazo desconhecido"
    try:
        dias = (datetime.fromisoformat(alvo[:19]) - agora).total_seconds() / 86400
    except ValueError:
        return None, f"prazo ilegível: {alvo}"
    if dias < 0:
        return 0.0, f"{origem} vencido em {alvo[:10]}"
    return _linear(dias, 0, cheio), f"{dias:.0f} dias até o {origem} ({alvo[:10]})"


def avaliar_item(item: Dict, sinais: set, cot: Optional[Dict], p: Premissas,
                 agora: Optional[datetime] = None) -> Dict:
    """Ficha + nota de um item. `item` vem de relatorio.itens() com o encerramento da compra."""
    agora = agora or agora_brasilia()
    cot = cot or {}
    criterios: Dict[str, Optional[float]] = {}
    motivos: Dict[str, str] = {}

    criterios["admissao"], motivos["admissao"] = _admissao(sinais)

    referencia = cot.get("referencia_brl")
    if referencia is None and item.get("valor_unitario_estimado") and not item.get("orcamento_sigiloso"):
        referencia = item["valor_unitario_estimado"]
    custo = p.custo(cot["preco_inr"]) if cot.get("preco_inr") else None
    margem = None
    if custo is not None and referencia:
        margem = (referencia - custo) / referencia
        criterios["margem"] = _linear(margem, p.margem_zero, p.margem_cheia)
        tipo = cot.get("referencia_tipo") or "referência"
        motivos["margem"] = (f"custo desembarcado R$ {custo:,.2f} contra {tipo} R$ {referencia:,.2f} "
                             f"(margem {margem:+.0%})").replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        criterios["margem"] = None
        falta = "preço indiano" if custo is None else "valor de referência"
        motivos["margem"] = f"sem {falta}"

    n = cot.get("fabricantes_ich_oms")
    if n is None:
        criterios["fornecedor"], motivos["fornecedor"] = None, "fabricantes ICH/OMS não cotados"
    else:
        criterios["fornecedor"] = {0: 0.0, 1: 0.5, 2: 0.8}.get(int(n), 1.0)
        motivos["fornecedor"] = f"{int(n)} fabricante(s) com registro ICH ou pré-qualificação OMS"

    criterios["prazo"], motivos["prazo"] = _prazo(item.get("fase") or "", item.get("data_encerramento"),
                                                 cot.get("prazo_manual"), agora, p.prazo_cheio_dias)

    cap = cot.get("capacidade_fornecedor")
    if not cap or not item.get("quantidade"):
        criterios["escala"], motivos["escala"] = None, "capacidade típica do fornecedor não informada"
    else:
        razao = item["quantidade"] / cap
        criterios["escala"] = 1.0 if razao <= 1 else _linear(-razao, -4, -1)
        motivos["escala"] = f"quantidade = {razao:.1f}× a capacidade típica de um fornecedor"

    # Criterio sem dado conta como neutro (50): um item so sobe na lista com
    # evidencia a favor, e a cobertura diz quanto da nota veio de dado real.
    peso_total = sum(p.pesos.values()) or 1
    peso_av = sum(p.pesos.get(k, 0) for k, v in criterios.items() if v is not None)
    # floor(x + 0,5): arredonda 72,5 para 73 (o round() do Python daria 72).
    nota = math.floor(100 * sum(p.pesos.get(k, 0) * (NEUTRO if v is None else v)
                                for k, v in criterios.items()) / peso_total + 0.5)

    return {
        "criterios": criterios, "motivos": motivos, "nota": nota,
        "cobertura": round(100 * peso_av / peso_total),
        "custo_desembarcado": custo, "referencia": referencia, "margem": margem,
        "fob_maximo_inr": p.fob_maximo_inr(referencia) if referencia else None,
        "cotacao": cot,
    }


def fichas(con: sqlite3.Connection, p: Premissas, cotacoes: Dict[tuple, Dict],
           numero_controle: Optional[str] = None, agora: Optional[datetime] = None) -> List[Dict]:
    """Uma ficha por item: das candidatas, de uma contratacao, ou de qualquer item cotado."""
    from . import relatorio

    if numero_controle:
        compras = [numero_controle]
    else:
        ids = {r["numero_controle"] for r in con.execute(
            "SELECT numero_controle FROM lic_compra WHERE candidata = 1")}
        cotadas = {c for (c, _i) in cotacoes}
        ids |= {r["numero_controle"] for r in con.execute(
            f"SELECT numero_controle FROM lic_compra WHERE identificador IN ({','.join('?' * len(cotadas))})",
            tuple(cotadas))} if cotadas else set()
        compras = sorted(ids)
    saida = []
    for nc in compras:
        c = dict(con.execute("SELECT * FROM lic_compra WHERE numero_controle = ?", (nc,)).fetchone())
        sinais = {r["sinal_id"] for r in con.execute(
            "SELECT DISTINCT sinal_id FROM lic_sinal WHERE numero_controle = ?", (nc,))}
        for it in relatorio.itens(con, nc):
            it["data_encerramento"] = c["data_encerramento"]
            av = avaliar_item(it, sinais, cotacoes.get((c["identificador"], it["numero_item"])), p, agora)
            saida.append({"compra": c, "item": it, "sinais": sorted(sinais), **av})
    saida.sort(key=lambda f: (-f["nota"], -f["cobertura"]))
    return saida


# -- Saidas ----------------------------------------------------------------

def _brl(v) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto_lista(lista: List[Dict]) -> str:
    if not lista:
        return "Nenhum item para avaliar."
    sem_preco = sum(1 for f in lista if f["criterios"]["margem"] is None)
    abertos = sum(1 for f in lista if f["criterios"]["prazo"])
    linhas = [f"{len(lista)} itens | {abertos} com prazo aberto | {sem_preco} sem margem calculada "
              f"(falta preço indiano ou referência em cotacoes.csv)", "",
              "Nota | Cob. | Pregão / contratação        | Item | Medicamento                          | Fase",
              "-----|------|-----------------------------|------|--------------------------------------|-----"]
    for f in lista:
        c, i = f["compra"], f["item"]
        ref = f"PE {c['numero_pregao']}" if c.get("numero_pregao") else c["identificador"]
        nota = "  — " if f["nota"] is None else f"{f['nota']:>4}"
        linhas.append(f"{nota} | {f['cobertura']:>3}% | {ref[:27]:<27} | {i['numero_item']:>4} | "
                      f"{(i['descricao'] or '')[:36]:<36} | {FASES.get(i['fase'], i['situacao'] or '')}")
        ruins = [f"{NOMES_CRITERIOS[k].lower()}: {f['motivos'][k]}" for k, v in f["criterios"].items()
                 if v is not None and v < 0.5]
        boas = [f"{NOMES_CRITERIOS[k].lower()}: {f['motivos'][k]}" for k, v in f["criterios"].items()
                if v is not None and v >= 0.5]
        if boas:
            linhas.append(f"     a favor: {'; '.join(boas)}")
        if ruins:
            linhas.append(f"     contra:  {'; '.join(ruins)}")
    return "\n".join(linhas)


def texto_ficha(f: Dict) -> str:
    c, i, cot = f["compra"], f["item"], f["cotacao"]
    ref = f"PE {c['numero_pregao']}" if c.get("numero_pregao") else "pregão n/d"
    linhas = [
        f"Ficha — {ref} | contratação {c['identificador']} | item {i['numero_item']}",
        f"  Identificação: UASG {c['uasg']} | {i['descricao']} | princípio ativo: {i['principio'] or '—'}",
        f"  Quantidade: {i['quantidade']:,.0f} {i['unidade'] or ''}".replace(",", "."),
        f"  Valor de referência: {_brl(f['referencia'])}"
        + ("" if f["referencia"] else (" (orçamento sigiloso no PNCP)" if i.get("orcamento_sigiloso") else "")),
        f"  Regras de elegibilidade (sinais do edital): "
        f"{', '.join(s.replace('_', ' ') for s in f['sinais']) or 'nenhum'}",
        f"  Resultado: {FASES.get(i['fase'], i['situacao'] or '?')}"
        + (f" — {i['vencedor']} {_brl(i['valor_unitario_homologado'])}/un" if i.get("vencedor") else ""),
        f"  Prazos: fim das propostas {(c['data_encerramento'] or '—')[:16]}"
        + (f"; prazo informado {cot['prazo_manual']}" if cot.get("prazo_manual") else ""),
        f"  Custo desembarcado: {_brl(f['custo_desembarcado'])} | FOB máximo que cabe na referência: "
        + (f"₹ {f['fob_maximo_inr']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
           if f["fob_maximo_inr"] else "—")
        + (f" | menor oferta no pregão: {_brl(cot.get('menor_oferta_brl'))}" if cot.get("menor_oferta_brl") else ""),
        f"  Nota de viabilidade: {f['nota'] if f['nota'] is not None else '—'} / 100 "
        f"(cobertura {f['cobertura']}% dos pesos)",
    ]
    for k, v in f["criterios"].items():
        nota = "  —" if v is None else f"{round(100 * v):>3}"
        linhas.append(f"     {nota}  {NOMES_CRITERIOS[k]}: {f['motivos'][k]}")
    if cot.get("observacao"):
        linhas.append(f"  Observações: {cot['observacao']}")
    linhas.append(f"  Fontes: {c['url_pncp']} (PNCP, lido em {(c['ultima_leitura'] or '')[:10]})"
                  + (f"; cotação: {cot['fonte']} ({cot.get('data')})" if cot.get("fonte") else ""))
    return "\n".join(linhas)


def gravar_csv(lista: List[Dict], pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / "licitacoes_viabilidade.csv"
    with caminho.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["nota", "cobertura_%", "pregao", "contratacao", "item", "descricao", "quantidade",
                    "unidade", "fase", "referencia_brl", "custo_desembarcado_brl", "margem",
                    "fob_maximo_inr", "menor_oferta_brl"]
                   + [f"{k}_nota" for k in NOMES_CRITERIOS] + [f"{k}_motivo" for k in NOMES_CRITERIOS]
                   + ["url_pncp"])
        for f in lista:
            c, i = f["compra"], f["item"]
            w.writerow([f["nota"] if f["nota"] is not None else "", f["cobertura"], c.get("numero_pregao") or "",
                        c["identificador"], i["numero_item"], i["descricao"], i["quantidade"], i["unidade"],
                        FASES.get(i["fase"], ""), _fmt(f["referencia"]), _fmt(f["custo_desembarcado"]),
                        _fmt(f["margem"]), _fmt(f["fob_maximo_inr"]), _fmt(f["cotacao"].get("menor_oferta_brl"))]
                       + ["" if f["criterios"][k] is None else round(100 * f["criterios"][k]) for k in NOMES_CRITERIOS]
                       + [f["motivos"][k] for k in NOMES_CRITERIOS] + [c["url_pncp"]])
    return caminho


def _fmt(v):
    return "" if v is None else round(v, 4)

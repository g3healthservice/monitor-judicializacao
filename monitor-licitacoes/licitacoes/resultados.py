"""Etapa 2: resultado por item (situacao, vencedor, ata) e historico de fracasso.

Fonte: API publica do PNCP. O PNCP informa a situacao oficial de cada item:

    1 Em andamento | 2 Homologado | 3 Anulado/Revogado/Cancelado | 4 Deserto | 5 Fracassado

A fase interna da sessao (aceitacao, habilitacao, diligencia) nao e publicada
pelo PNCP. Um item "Em andamento" depois do fim do prazo de propostas aparece
aqui como "em_julgamento": e nessa fase que ficam as diligencias.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from .relogio import agora_brasilia
from . import db
from .texto import normalizar

SITUACOES = {1: "Em andamento", 2: "Homologado", 3: "Anulado/Revogado/Cancelado",
             4: "Deserto", 5: "Fracassado"}
FINAIS = {2, 3, 4, 5}
SEM_SUCESSO = {3, 4, 5}  # fracassado, deserto ou cancelado

# Rotulos das fases derivadas, na ordem em que um item passa por elas.
FASES = {
    "propostas_abertas": "Propostas abertas",
    "em_julgamento": "Em julgamento (aceitação, habilitação ou diligência)",
    "homologado": "Homologado",
    "fracassado": "Fracassado",
    "deserto": "Deserto",
    "cancelado": "Anulado/revogado/cancelado",
}

# Onde termina o nome do principio ativo na descricao CATMAT.
_RE_CORTE = re.compile(
    r"\s*(?:,|:|;|\(|\bconcentra|\bdosagem|\bcomposi|\bforma\s*farm|\bcaracter|\batividade"
    r"|\bprincipio|\bapresenta|\btipo\b|\borigem\b|\bvolume|\baplica|\bentrega|\s\d)")
# Sais e termos que nao mudam o principio ativo para fins de historico.
_SAIS = {
    "sal", "cloridrato", "dicloridrato", "citrato", "besilato", "acetato", "maleato", "sulfato",
    "fosfato", "mesilato", "etexilato", "bitartarato", "sodico", "sodica", "potassico",
    "potassica", "calcico", "hidrobrometo", "tartarato", "fumarato", "succinato", "de", "do",
    "da", "monoidratado", "di-hidratado", "hidratado", "base",
}
# Nomes que so identificam o produto junto com o tipo (fator IX, insulina NPH...).
# Sem o tipo na descricao, o item fica fora do historico para nao juntar
# produtos diferentes.
_GENERICOS = ("concentrado fator", "fator coagulacao", "insulina", "imunoglobulina", "vacina",
              "soro", "reagente")
_RE_TIPO = re.compile(r"\b(?:fator|tipo)\s*:\s*([^,;]+)")


def principio(descricao: Optional[str]) -> Optional[str]:
    """'Trazodona cloridrato' e 'TRAZODONA CONCENTRAÇÃO: 50' -> 'trazodona'."""
    if not descricao:
        return None
    norm = normalizar(descricao).strip()
    corte = next((m.start() for m in _RE_CORTE.finditer(norm) if m.start() > 0), len(norm))
    palavras = [p for p in re.split(r"[\s/]+", norm[:corte]) if p and p not in _SAIS]
    nome = " ".join(palavras) or None
    if nome and nome.startswith(_GENERICOS):
        tipo = _RE_TIPO.search(norm)
        if not tipo:
            return None
        base = [p for p in re.split(r"[\s/]+", norm[:min(corte, tipo.start())]) if p and p not in _SAIS]
        nome = f"{' '.join(base)} {' '.join(tipo.group(1).split())}"
    return nome


def fase(situacao_id: Optional[int], data_encerramento: Optional[str],
         agora: Optional[datetime] = None) -> str:
    if situacao_id == 2:
        return "homologado"
    if situacao_id == 5:
        return "fracassado"
    if situacao_id == 4:
        return "deserto"
    if situacao_id == 3:
        return "cancelado"
    agora = agora or agora_brasilia()
    if data_encerramento:
        try:
            if datetime.fromisoformat(data_encerramento[:19]) > agora:
                return "propostas_abertas"
        except ValueError:
            pass
        return "em_julgamento"
    return "propostas_abertas"


class Acompanhamento:
    def __init__(self, con: sqlite3.Connection, cliente):
        self.con = con
        self.cliente = cliente

    def estado_anterior(self, nc: str) -> Dict[int, Dict]:
        return {r["numero_item"]: dict(r) for r in self.con.execute(
            "SELECT numero_item, situacao_id, situacao, resultado_versao, data_atualizacao, "
            "valor_unitario_estimado "
            "FROM lic_item WHERE numero_controle = ?", (nc,))}

    def atualizar(self, nc: str, cnpj: str, ano: int, seq: int, itens: List[Dict],
                  antes: Dict[int, Dict], ts: str) -> Dict[str, int]:
        """Grava situacao/fase dos itens, eventos de mudanca, resultados e atas."""
        enc = self.con.execute("SELECT data_encerramento FROM lic_compra WHERE numero_controle = ?",
                               (nc,)).fetchone()
        encerramento = enc["data_encerramento"] if enc else None
        cont = {"eventos": 0, "resultados": 0, "atas": 0}
        # Primeira leitura dos resultados desta contratacao: registra o estado
        # atual sem gerar eventos (senao a carga inicial vira uma enxurrada de alertas).
        self._silencioso = not any(v.get("situacao_id") is not None for v in antes.values())
        with db.transacao(self.con):
            for i in itens:
                n = i.get("numeroItem")
                sid = i.get("situacaoCompraItem")
                sid = int(sid) if sid is not None else None
                self.con.execute(
                    "UPDATE lic_item SET situacao_id=?, principio=?, fase=? "
                    "WHERE numero_controle=? AND numero_item=?",
                    (sid, principio(i.get("descricao")), fase(sid, encerramento), nc, n))
                velho = antes.get(n)
                ref_velha, ref_nova = (velho or {}).get("valor_unitario_estimado"), i.get("valorUnitarioEstimado")
                if velho and ref_velha and ref_nova and abs(ref_velha - ref_nova) > 0.005:
                    self._evento(nc, n, "referencia", f"{ref_velha:.2f}", f"{ref_nova:.2f}",
                                 "valor unitário estimado mudou no PNCP", ts)
                    cont["eventos"] += 1
                if velho and velho.get("situacao_id") is not None and velho["situacao_id"] != sid:
                    self._evento(nc, n, "situacao", velho.get("situacao"),
                                 i.get("situacaoCompraItemNome") or SITUACOES.get(sid), None, ts)
                    cont["eventos"] += 1

        for i in itens:
            n = i.get("numeroItem")
            versao = i.get("dataAtualizacao")
            if not i.get("temResultado"):
                continue
            if (antes.get(n) or {}).get("resultado_versao") == versao:
                continue
            cont["resultados"] += self._resultados(nc, cnpj, ano, seq, n, versao, ts)

        cont["atas"] += self._atas(nc, cnpj, ano, seq, ts)
        if not self._silencioso:
            cont["eventos"] += cont["resultados"] + cont["atas"]
        return cont

    def _evento(self, nc, item, tipo, de, para, detalhe, ts) -> None:
        if getattr(self, "_silencioso", False):
            return
        self.con.execute(
            "INSERT INTO lic_evento (numero_controle, numero_item, tipo, de, para, detalhe, detectado_em) "
            "VALUES (?,?,?,?,?,?,?)", (nc, item, tipo, de, para, detalhe, ts))

    def _resultados(self, nc, cnpj, ano, seq, item, versao, ts) -> int:
        novos = 0
        regs = self.cliente.resultados(cnpj, ano, seq, item)
        with db.transacao(self.con):
            for r in regs:
                chave = (nc, item, int(r.get("sequencialResultado") or 1))
                existia = self.con.execute(
                    "SELECT 1 FROM lic_resultado WHERE numero_controle=? AND numero_item=? AND sequencial_resultado=?",
                    chave).fetchone()
                self.con.execute(
                    """INSERT INTO lic_resultado (numero_controle, numero_item, sequencial_resultado,
                           fornecedor_ni, fornecedor_nome, fornecedor_porte, codigo_pais,
                           pais_origem_produto, quantidade_homologada, valor_unitario_homologado,
                           valor_total_homologado, moeda_estrangeira, valor_moeda_estrangeira,
                           ordem_classificacao, situacao, data_resultado, data_cancelamento,
                           motivo_cancelamento, lido_em)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(numero_controle, numero_item, sequencial_resultado) DO UPDATE SET
                           fornecedor_ni=excluded.fornecedor_ni, fornecedor_nome=excluded.fornecedor_nome,
                           fornecedor_porte=excluded.fornecedor_porte, codigo_pais=excluded.codigo_pais,
                           pais_origem_produto=excluded.pais_origem_produto,
                           quantidade_homologada=excluded.quantidade_homologada,
                           valor_unitario_homologado=excluded.valor_unitario_homologado,
                           valor_total_homologado=excluded.valor_total_homologado,
                           moeda_estrangeira=excluded.moeda_estrangeira,
                           valor_moeda_estrangeira=excluded.valor_moeda_estrangeira,
                           ordem_classificacao=excluded.ordem_classificacao, situacao=excluded.situacao,
                           data_resultado=excluded.data_resultado,
                           data_cancelamento=excluded.data_cancelamento,
                           motivo_cancelamento=excluded.motivo_cancelamento, lido_em=excluded.lido_em""",
                    (*chave, r.get("niFornecedor"), r.get("nomeRazaoSocialFornecedor"),
                     r.get("porteFornecedorNome"), r.get("codigoPais"),
                     _nome(r.get("paisOrigemProdutoServico")),
                     r.get("quantidadeHomologada"), r.get("valorUnitarioHomologado"),
                     r.get("valorTotalHomologado"), _nome(r.get("moedaEstrangeira")),
                     r.get("valorNominalMoedaEstrangeira"), r.get("ordemClassificacaoSrp"),
                     r.get("situacaoCompraItemResultadoNome"), r.get("dataResultado"),
                     r.get("dataCancelamento"), r.get("motivoCancelamento"), ts))
                if not existia:
                    novos += 1
                    self._evento(nc, item, "resultado", None, r.get("nomeRazaoSocialFornecedor"),
                                 f"R$ {r.get('valorUnitarioHomologado')} por unidade", ts)
            self.con.execute("UPDATE lic_item SET resultado_versao=? WHERE numero_controle=? AND numero_item=?",
                             (versao, nc, item))
        return novos

    def _atas(self, nc, cnpj, ano, seq, ts) -> int:
        novas = 0
        regs = self.cliente.atas(cnpj, ano, seq)
        with db.transacao(self.con):
            for a in regs:
                ida = a.get("numeroControlePNCP")
                if not ida:
                    continue
                existia = self.con.execute("SELECT 1 FROM lic_ata WHERE numero_controle_ata=?", (ida,)).fetchone()
                self.con.execute(
                    """INSERT INTO lic_ata (numero_controle_ata, numero_controle, numero_ata, ano_ata,
                           data_assinatura, vigencia_inicio, vigencia_fim, cancelado, data_cancelamento, lido_em)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(numero_controle_ata) DO UPDATE SET
                           data_assinatura=excluded.data_assinatura, vigencia_inicio=excluded.vigencia_inicio,
                           vigencia_fim=excluded.vigencia_fim, cancelado=excluded.cancelado,
                           data_cancelamento=excluded.data_cancelamento, lido_em=excluded.lido_em""",
                    (ida, nc, a.get("numeroAtaRegistroPreco"), a.get("anoAta"), a.get("dataAssinatura"),
                     a.get("dataVigenciaInicio"), a.get("dataVigenciaFim"), int(bool(a.get("cancelado"))),
                     a.get("dataCancelamento"), ts))
                if not existia:
                    novas += 1
                    self._evento(nc, None, "ata", None, f"Ata {a.get('numeroAtaRegistroPreco')}/{a.get('anoAta')}",
                                 f"vigência {a.get('dataVigenciaInicio')} a {a.get('dataVigenciaFim')}", ts)
        return novas

    # -- Historico: o mesmo principio ativo ja fracassou antes? -----------
    def sinal_historico(self, nc: str, ts: str, nivel: str, peso: int) -> int:
        """Grava o sinal 'historico_fracasso' (sequencial_documento = -1).

        Procura, entre as contratacoes ja coletadas e publicadas antes desta,
        itens com o mesmo principio ativo que terminaram fracassados, desertos
        ou cancelados. Retorna quantos itens desta contratacao tem esse historico.
        """
        atual = self.con.execute("SELECT data_publicacao FROM lic_compra WHERE numero_controle=?",
                                 (nc,)).fetchone()
        if not atual:
            return 0
        achados = self.con.execute(
            f"""SELECT DISTINCT i.numero_item, i.principio, o.numero_item AS item_ant,
                       o.situacao AS situacao_ant, c.identificador, c.numero_pregao, c.url_pncp,
                       c.data_publicacao
                FROM lic_item i
                JOIN lic_item o ON o.principio = i.principio AND o.numero_controle <> i.numero_controle
                JOIN lic_compra c ON c.numero_controle = o.numero_controle
                WHERE i.numero_controle = ? AND i.principio IS NOT NULL
                  AND o.situacao_id IN ({','.join(map(str, SEM_SUCESSO))})
                  AND c.data_publicacao < ?
                ORDER BY i.numero_item, c.data_publicacao DESC""",
            (nc, atual["data_publicacao"])).fetchall()
        with db.transacao(self.con):
            self.con.execute(
                "DELETE FROM lic_sinal WHERE numero_controle=? AND sequencial_documento=-1", (nc,))
            if not achados:
                return 0
            frases, itens = [], set()
            for a in achados:
                itens.add(a["numero_item"])
                pregao = f"PE {a['numero_pregao']}" if a["numero_pregao"] else f"contratação {a['identificador']}"
                frases.append(f"item {a['numero_item']} ({a['principio']}): {a['situacao_ant'].lower()} "
                              f"em {pregao}, item {a['item_ant']}, publicado em {a['data_publicacao'][:10]}")
            self.con.execute(
                """INSERT INTO lic_sinal (numero_controle, sequencial_documento, sinal_id, nivel, peso,
                       trecho, pagina, ocorrencias, fonte_url, lido_em)
                   VALUES (?, -1, 'historico_fracasso', ?, ?, ?, NULL, ?, ?, ?)""",
                (nc, nivel, peso, "; ".join(frases[:8]) + ("; ..." if len(frases) > 8 else ""),
                 len(frases), achados[0]["url_pncp"], ts))
        return len(itens)


def _nome(valor):
    if isinstance(valor, dict):
        return valor.get("nome") or valor.get("descricao")
    return valor

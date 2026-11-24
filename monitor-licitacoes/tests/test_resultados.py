"""Etapa 2: situacao por item, eventos, resultados, atas e historico de fracasso."""
from datetime import datetime
from pathlib import Path

import pytest

from licitacoes import db, relatorio
from licitacoes.coleta import Coletor
from licitacoes.config import Config
from licitacoes.resultados import fase, principio
from licitacoes.sinais import carregar_regras

from .test_coleta import REG_709, ClienteFalso

REGRAS = carregar_regras(Path(__file__).resolve().parents[1] / "licitacoes" / "sinais.yaml")


@pytest.mark.parametrize("desc,esperado", [
    ("TOLVAPTANA CONCENTRAÇÃO: 15", "tolvaptana"),
    ("Tolvaptana concentração: 30", "tolvaptana"),
    ("Trazodona cloridrato", "trazodona"),
    ("SILDENAFILA COMPOSIÇÃO: SAL CITRATO, CONCENTRAÇÃO: 50", "sildenafila"),
    ("Dabigatrana Etexilato concentração: 75", "dabigatrana"),
    ("Ácido Ursodesoxicólico concentração: 300", "acido ursodesoxicolico"),
    ("COMPLEXO PROTROMBÍNICO ATIVIDADE: 500, FORMA FARMACÊUTICA: PÓ", "complexo protrombinico"),
    ("Pitolisant concentração: 17,8", "pitolisant"),
    ("Alfa-1 Antitripsina (Aat) concentração: 1000", "alfa-1 antitripsina"),
    ("CONCENTRADO DE FATOR DE COAGULAÇÃO FATOR: FATOR IX, FORMA FARMACÊUTICA: PÓ", "concentrado fator coagulacao fator ix"),
    ("Concentrado De Fator De Coagulação", None),
    ("INSULINA TIPO: NPH, ORIGEM: HUMANA, DOSAGEM: 100U/ML", "insulina nph"),
    ("Insulina", None),
    ("ALFAEPOETINA CONCENTRAÇÃO: 2.000, FORMA FARMACEUTICA: INJETÁVEL Entrega descentralizada", "alfaepoetina"),
])
def test_principio(desc, esperado):
    assert principio(desc) == esperado


def test_fase():
    agora = datetime(2026, 10, 1)
    assert fase(1, "2026-10-15T10:00:00", agora) == "propostas_abertas"
    assert fase(1, "2026-09-15T10:00:00", agora) == "em_julgamento"
    assert [fase(s, None) for s in (2, 3, 4, 5)] == ["homologado", "cancelado", "deserto", "fracassado"]


REG_ANTERIOR = dict(REG_709, numeroControlePNCP="00394544000185-1-001000/2026", sequencialCompra=1000,
                    numeroCompra="500", objetoCompra="PE 90050/2026 - Aquisição de Tolvaptana",
                    dataPublicacaoPncp="2026-05-01T04:00:00", dataAtualizacaoGlobal="2026-06-01T00:00:00")


class ClienteComResultado(ClienteFalso):
    """709 com dois itens; uma contratacao anterior em que a tolvaptana fracassou."""

    def __init__(self):
        super().__init__()
        self.situacao_709 = 1

    def contratacoes(self, ini, fim, mod, uasg=None, por="atualizacao"):
        return iter([REG_ANTERIOR, REG_709])

    def itens(self, cnpj, ano, seq):
        if seq == 1000:
            return [{"numeroItem": 1, "descricao": "TOLVAPTANA CONCENTRAÇÃO: 15", "quantidade": 100,
                     "unidadeMedida": "Comprimido", "ncmNbsCodigo": "30049099",
                     "situacaoCompraItem": 5, "situacaoCompraItemNome": "Fracassado",
                     "dataAtualizacao": "2026-06-01T00:00:00"}]
        homolog = self.situacao_709 == 2
        return [
            {"numeroItem": 1, "descricao": "Tolvaptana concentração: 15", "quantidade": 20340,
             "unidadeMedida": "Comprimido", "ncmNbsCodigo": "30049099",
             "situacaoCompraItem": self.situacao_709,
             "situacaoCompraItemNome": "Homologado" if homolog else "Em andamento",
             "temResultado": homolog, "dataAtualizacao": "2026-10-20" if homolog else "2026-09-28"},
            {"numeroItem": 2, "descricao": "Quinidina Sulfato dosagem: 200", "quantidade": 12150,
             "unidadeMedida": "Comprimido", "ncmNbsCodigo": "30049099", "situacaoCompraItem": 1,
             "situacaoCompraItemNome": "Em andamento", "dataAtualizacao": "2026-09-28"},
        ]

    def arquivos(self, cnpj, ano, seq):
        return [] if seq == 1000 else super().arquivos(cnpj, ano, seq)

    def resultados(self, cnpj, ano, seq, item):
        if seq == 2092 and item == 1 and self.situacao_709 == 2:
            return [{"sequencialResultado": 1, "niFornecedor": "11111111000191",
                     "nomeRazaoSocialFornecedor": "IMPORTADORA EXEMPLO LTDA", "codigoPais": "BRA",
                     "quantidadeHomologada": 20340, "valorUnitarioHomologado": 12.5,
                     "valorTotalHomologado": 254250, "situacaoCompraItemResultadoNome": "Informado",
                     "dataResultado": "2026-10-20"}]
        return []

    def atas(self, cnpj, ano, seq):
        if seq == 2092 and self.situacao_709 == 2:
            return [{"numeroControlePNCP": "00394544000185-1-002092/2026-000001",
                     "numeroAtaRegistroPreco": "00300", "anoAta": 2026,
                     "dataVigenciaInicio": "2026-10-25", "dataVigenciaFim": "2027-10-25"}]
        return []


def test_etapa2_ponta_a_ponta(tmp_path):
    cfg = Config(db_path=str(tmp_path / "m.db"), saida_dir=tmp_path / "out")
    con = db.conectar(cfg.db_path)
    cli = ClienteComResultado()
    Coletor(cfg, REGRAS, con, cli).coletar()

    # Historico: a tolvaptana fracassou no PE 90050/2026, publicado antes do 90114.
    sinal = con.execute("SELECT * FROM lic_sinal WHERE sinal_id='historico_fracasso'").fetchall()
    assert len(sinal) == 1
    assert "PE 90050/2026" in sinal[0]["trecho"] and "fracassado" in sinal[0]["trecho"]
    assert sinal[0]["numero_controle"] == REG_709["numeroControlePNCP"]
    c = relatorio.buscar(con, "90114")[0]
    assert c["pontuacao"] == 13 + 3

    itens = {i["numero_item"]: i for i in relatorio.itens(con, c["numero_controle"])}
    assert itens[1]["fase"] == "em_julgamento"  # encerramento 15/10/2026 ja passou

    # O item 1 e homologado: acompanhar detecta a mudanca, o vencedor e a ata.
    cli.situacao_709 = 2
    stats = Coletor(cfg, REGRAS, con, cli).acompanhar()
    assert stats["eventos"] == 3
    eventos = [(e["tipo"], e["numero_item"], e["para"]) for e in relatorio.eventos(con)]
    assert ("situacao", 1, "Homologado") in eventos
    assert ("resultado", 1, "IMPORTADORA EXEMPLO LTDA") in eventos
    assert ("ata", None, "Ata 00300/2026") in eventos

    itens = {i["numero_item"]: i for i in relatorio.itens(con, c["numero_controle"])}
    assert itens[1]["fase"] == "homologado"
    assert itens[1]["vencedor"] == "IMPORTADORA EXEMPLO LTDA"
    assert itens[1]["valor_unitario_homologado"] == 12.5
    assert itens[2]["fase"] == "em_julgamento"

    # Sem mudanca no PNCP, uma nova rodada nao gera evento.
    stats = Coletor(cfg, REGRAS, con, cli).acompanhar()
    assert stats["eventos"] == 0

"""Tabelas do monitor de licitacoes no mesmo SQLite do monitor de judicializacao.

Todas as tabelas usam o prefixo lic_ para nao colidir com as tabelas existentes.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

ESQUEMA = """
CREATE TABLE IF NOT EXISTS lic_compra (
    numero_controle     TEXT PRIMARY KEY,   -- numeroControlePNCP
    cnpj                TEXT NOT NULL,
    ano                 INTEGER NOT NULL,
    sequencial          INTEGER NOT NULL,
    uasg                TEXT,
    unidade_nome        TEXT,
    numero_compra       TEXT,               -- contratacao no Compras.gov.br (ex.: 709)
    numero_pregao       TEXT,               -- pregao citado no objeto/arquivos (ex.: 90114)
    identificador       TEXT,               -- UASG-compra-ano (ex.: 250005-709-2026)
    modalidade          TEXT,
    objeto              TEXT,
    situacao            TEXT,
    processo            TEXT,
    srp                 INTEGER,
    data_publicacao     TEXT,
    data_abertura       TEXT,
    data_encerramento   TEXT,
    data_atualizacao    TEXT,               -- dataAtualizacaoGlobal do PNCP
    link_origem         TEXT,
    url_pncp            TEXT,
    eh_medicamento      INTEGER,
    pontuacao           INTEGER DEFAULT 0,
    tem_alto            INTEGER DEFAULT 0,
    candidata           INTEGER DEFAULT 0,
    primeira_leitura    TEXT,
    ultima_leitura      TEXT,
    avaliada_em         TEXT,
    avaliada_versao     TEXT                -- data_atualizacao avaliada (reavalia se mudar)
);

CREATE TABLE IF NOT EXISTS lic_item (
    numero_controle     TEXT NOT NULL,
    numero_item         INTEGER NOT NULL,
    descricao           TEXT,
    quantidade          REAL,
    unidade             TEXT,
    ncm                 TEXT,
    catmat              TEXT,
    valor_unitario_estimado REAL,
    valor_total         REAL,
    orcamento_sigiloso  INTEGER,
    situacao            TEXT,
    tem_resultado       INTEGER,
    data_atualizacao    TEXT,
    lido_em             TEXT,
    PRIMARY KEY (numero_controle, numero_item)
);

CREATE TABLE IF NOT EXISTS lic_arquivo (
    numero_controle     TEXT NOT NULL,
    sequencial_documento INTEGER NOT NULL,
    titulo              TEXT,
    tipo                TEXT,
    url                 TEXT,
    data_publicacao     TEXT,
    ativo               INTEGER,
    sha256              TEXT,
    paginas             INTEGER,
    caracteres          INTEGER,
    lido_em             TEXT,
    erro                TEXT,
    PRIMARY KEY (numero_controle, sequencial_documento)
);

-- Um registro por (contratacao, documento, sinal). sequencial_documento = 0
-- quando o sinal veio do texto do objeto da contratacao.
CREATE TABLE IF NOT EXISTS lic_sinal (
    numero_controle     TEXT NOT NULL,
    sequencial_documento INTEGER NOT NULL,
    sinal_id            TEXT NOT NULL,
    nivel               TEXT,
    peso                INTEGER,
    trecho              TEXT,
    pagina              INTEGER,
    ocorrencias         INTEGER,
    fonte_url           TEXT,
    lido_em             TEXT,
    PRIMARY KEY (numero_controle, sequencial_documento, sinal_id)
);

CREATE TABLE IF NOT EXISTS lic_checkpoint (
    chave   TEXT PRIMARY KEY,
    valor   TEXT
);

CREATE INDEX IF NOT EXISTS ix_lic_compra_candidata ON lic_compra (candidata, pontuacao);

-- Etapa 2: resultado por item -------------------------------------------

-- Vencedor(es) de cada item, como o PNCP publica apos a homologacao.
CREATE TABLE IF NOT EXISTS lic_resultado (
    numero_controle     TEXT NOT NULL,
    numero_item         INTEGER NOT NULL,
    sequencial_resultado INTEGER NOT NULL,
    fornecedor_ni       TEXT,               -- CNPJ do fornecedor (dado publico)
    fornecedor_nome     TEXT,
    fornecedor_porte    TEXT,
    codigo_pais         TEXT,
    pais_origem_produto TEXT,
    quantidade_homologada REAL,
    valor_unitario_homologado REAL,
    valor_total_homologado REAL,
    moeda_estrangeira   TEXT,
    valor_moeda_estrangeira REAL,
    ordem_classificacao INTEGER,
    situacao            TEXT,
    data_resultado      TEXT,
    data_cancelamento   TEXT,
    motivo_cancelamento TEXT,
    lido_em             TEXT,
    PRIMARY KEY (numero_controle, numero_item, sequencial_resultado)
);

CREATE TABLE IF NOT EXISTS lic_ata (
    numero_controle_ata TEXT PRIMARY KEY,
    numero_controle     TEXT NOT NULL,
    numero_ata          TEXT,
    ano_ata             INTEGER,
    data_assinatura     TEXT,
    vigencia_inicio     TEXT,
    vigencia_fim        TEXT,
    cancelado           INTEGER,
    data_cancelamento   TEXT,
    lido_em             TEXT
);

-- Mudancas detectadas entre uma leitura e a seguinte (base dos alertas).
CREATE TABLE IF NOT EXISTS lic_evento (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_controle     TEXT NOT NULL,
    numero_item         INTEGER,
    tipo                TEXT NOT NULL,      -- situacao | resultado | ata
    de                  TEXT,
    para                TEXT,
    detalhe             TEXT,
    detectado_em        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lic_evento_data ON lic_evento (detectado_em);
"""

# Colunas acrescentadas depois da primeira versao (o banco pode ja existir).
_COLUNAS_NOVAS = {
    "lic_item": {
        "situacao_id": "INTEGER",
        "principio": "TEXT",            # principio ativo normalizado (para o historico)
        "fase": "TEXT",                 # situacao derivada, ver resultados.fase()
        "resultado_versao": "TEXT",     # data_atualizacao do item quando o resultado foi lido
    },
}


def conectar(caminho: str) -> sqlite3.Connection:
    con = sqlite3.connect(caminho, timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    for tabela, colunas in _COLUNAS_NOVAS.items():
        existentes = {r["name"] for r in con.execute(f"PRAGMA table_info({tabela})")}
        for nome, tipo in colunas.items():
            if nome not in existentes:
                con.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
    con.commit()
    return con


@contextmanager
def transacao(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def ler_checkpoint(con: sqlite3.Connection, chave: str) -> Optional[str]:
    r = con.execute("SELECT valor FROM lic_checkpoint WHERE chave = ?", (chave,)).fetchone()
    return r["valor"] if r else None


def gravar_checkpoint(con: sqlite3.Connection, chave: str, valor: str) -> None:
    con.execute(
        "INSERT INTO lic_checkpoint (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )

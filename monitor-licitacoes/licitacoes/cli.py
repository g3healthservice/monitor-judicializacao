"""Linha de comando: python -m licitacoes.cli <comando>."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import db, relatorio
from .coleta import Coletor
from .config import Config
from .sinais import carregar_regras


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="licitacoes", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="cria as tabelas lic_* no SQLite")

    c = sub.add_parser("coletar", help="coleta no PNCP e aplica as regras de sinal")
    c.add_argument("--desde", type=date.fromisoformat, help="AAAA-MM-DD (ignora o checkpoint)")
    c.add_argument("--ate", type=date.fromisoformat, help="AAAA-MM-DD (padrao: hoje)")
    c.add_argument("--por", choices=["atualizacao", "publicacao"], default="atualizacao")
    c.add_argument("--reprocessar", action="store_true", help="rele documentos ja avaliados")

    sub.add_parser("reavaliar", help="recalcula pontuacao apos mudar pesos/limiar (sem rede)")

    l = sub.add_parser("candidatas", help="lista as demandas candidatas e grava CSV/Markdown")
    l.add_argument("--todas", action="store_true", help="inclui medicamentos abaixo do limiar")
    l.add_argument("--abertas", action="store_true", help="so com prazo de proposta em aberto")
    l.add_argument("--json", action="store_true")

    v = sub.add_parser("ver", help="mostra uma contratacao pelo numero (709, 90114, 90114/2026...)")
    v.add_argument("numero")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    regras = carregar_regras(cfg.sinais_path)
    con = db.conectar(cfg.db_path)

    if a.cmd == "init-db":
        print(f"Tabelas lic_* prontas em {cfg.db_path}")
    elif a.cmd == "coletar":
        stats = Coletor(cfg, regras, con).coletar(a.desde, a.ate, a.por, a.reprocessar)
        print(json.dumps(stats, ensure_ascii=False))
    elif a.cmd == "reavaliar":
        print(f"{relatorio.reavaliar(con, regras)} contratacoes reavaliadas")
    elif a.cmd == "candidatas":
        lista = relatorio.candidatas(con, todas=a.todas, somente_abertas=a.abertas)
        arquivos = relatorio.gravar(lista, regras, cfg.saida_dir)
        if a.json:
            print(json.dumps(lista, ensure_ascii=False, indent=1, default=str))
        else:
            print(relatorio.texto_terminal(lista, regras))
            print(f"Gravado: {arquivos['csv']} e {arquivos['md']}")
    elif a.cmd == "ver":
        lista = relatorio.buscar(con, a.numero)
        print(relatorio.texto_terminal(lista, regras) if lista else f"{a.numero}: nao encontrado no banco")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI do monitor de judicializacao."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config.logging_setup import setup_logging
from .config.settings import get_settings, load_municipios, load_tribunais, municipios_por_tribunal
from .report.aggregate import agregar_municipio
from .report.dashboard import build_dataset, gerar_dashboard
from .report.pdf import gerar_pdf
from .report.xlsx import gerar_xlsx
from .store.db import get_session, init_db


def _cmd_init_db(args):
    init_db()
    print("Banco inicializado.")


def _cmd_run(args):
    from .pipeline import ingerir_tribunal

    settings = get_settings()
    init_db()

    if args.curado:
        # Modo curado: config/municipios.yaml (filtra por municipio, sem derivar UF).
        grupos = municipios_por_tribunal(load_municipios())
        alvos = [(trib, muns, None) for trib, muns in grupos.items()
                 if not args.tribunal or trib == args.tribunal]
    else:
        # Modo Brasil inteiro: config/tribunais.yaml (27 TJs), sem filtro de municipio.
        tribunais = load_tribunais()
        alvos = [(t.sigla, None, t.uf) for t in tribunais
                 if (not args.tribunal or t.sigla == args.tribunal)
                 and (not args.uf or t.uf == args.uf.upper())]

    async def _run():
        totais = {}
        for tribunal, muns, uf in alvos:
            # Resiliencia: falha de um tribunal (ex.: timeout do DataJud) nao
            # derruba os demais nem a automacao.
            try:
                totais[tribunal] = await ingerir_tribunal(
                    tribunal, muns, settings=settings,
                    max_paginas=args.max_paginas, uf=uf,
                )
            except Exception as exc:  # noqa: BLE001
                totais[tribunal] = {"erro": f"{type(exc).__name__}: {exc}"}
        return totais

    totais = asyncio.run(_run())
    print(json.dumps(totais, ensure_ascii=False, indent=2))


def _cmd_report(args):
    municipios = load_municipios()
    alvo = next((m for m in municipios if m.codigo_ibge == args.ibge), None)
    nome = alvo.nome if alvo else args.ibge
    out_dir = Path(args.out or "relatorios")

    with get_session() as session:
        metricas = agregar_municipio(session, args.ibge)

    xlsx = gerar_xlsx(metricas, out_dir / f"relatorio_{args.ibge}.xlsx", municipio_nome=nome)
    print(f"XLSX: {xlsx}")
    if not args.sem_pdf:
        pdf = gerar_pdf(metricas, out_dir / f"relatorio_{args.ibge}.pdf", municipio_nome=nome)
        print(f"PDF:  {pdf}")


def _cmd_dashboard(args):
    out = Path(args.out or "out") / "dashboard.html"
    with get_session() as session:
        # None -> agrupa por todos os municipios presentes no banco (Brasil inteiro).
        payload = build_dataset(session, None)
    destino = gerar_dashboard(payload, out)
    print(f"Dashboard: {destino}")
    print(f"Municípios: {payload['totais']['n_municipios']} · "
          f"Dinheiro na mesa: R$ {payload['totais']['valor_total_ressarcivel']:,.2f}")


def main(argv=None):
    setup_logging(get_settings().log_level)
    parser = argparse.ArgumentParser(prog="monitor", description="Monitor de judicializacao (Tema 1.234)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Cria as tabelas").set_defaults(func=_cmd_init_db)

    p_run = sub.add_parser("run", help="Executa ingestao incremental (requer DATAJUD_API_KEY)")
    p_run.add_argument("--tribunal", help="Restringe a um tribunal (ex.: tjsp)")
    p_run.add_argument("--uf", help="Restringe a uma UF (ex.: SP)")
    p_run.add_argument("--curado", action="store_true",
                       help="Usa config/municipios.yaml (filtra por municipio) em vez dos 27 TJs")
    p_run.add_argument("--max-paginas", type=int, default=None)
    p_run.set_defaults(func=_cmd_run)

    p_rep = sub.add_parser("report", help="Gera relatorio XLSX/PDF de um municipio")
    p_rep.add_argument("--ibge", required=True, help="Codigo IBGE do municipio")
    p_rep.add_argument("--out", help="Diretorio de saida (default: relatorios/)")
    p_rep.add_argument("--sem-pdf", action="store_true")
    p_rep.set_defaults(func=_cmd_report)

    p_dash = sub.add_parser("dashboard", help="Gera o dashboard HTML autocontido (online, padrao Raio-X)")
    p_dash.add_argument("--out", help="Diretorio de saida (default: out/)")
    p_dash.set_defaults(func=_cmd_dashboard)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

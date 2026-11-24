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

    ac = sub.add_parser("acompanhar", help="atualiza situacao, vencedor e ata dos itens pendentes")
    ac.add_argument("--candidatas", action="store_true", help="so as contratacoes candidatas")

    r = sub.add_parser("resultados", help="situacao por item das candidatas (ou de uma contratacao)")
    r.add_argument("numero", nargs="?", help="709, 90114, 90114/2026... (padrao: todas as candidatas)")

    e = sub.add_parser("eventos", help="mudancas detectadas (situacao, resultado, ata)")
    e.add_argument("--desde", help="AAAA-MM-DD")
    e.add_argument("--candidatas", action="store_true", help="so das contratacoes candidatas")

    vb = sub.add_parser("viabilidade", help="nota de viabilidade por item, ordenada, com o motivo")
    vb.add_argument("--abertas", action="store_true", help="so itens com prazo ainda aberto")

    fi = sub.add_parser("ficha", help="ficha completa de uma contratacao (ou de um item)")
    fi.add_argument("numero", help="709, 90114, 90114/2026...")
    fi.add_argument("item", nargs="?", type=int)

    si = sub.add_parser("simular", help="cenarios (cambio, imposto, frete, volume) dos itens cotados")
    si.add_argument("numero", nargs="?", help="709, 90114... (padrao: todos os itens com preco indiano)")
    si.add_argument("item", nargs="?", type=int)

    al = sub.add_parser("alertas", help="gera e envia os alertas novos (e-mail/Telegram)")
    al.add_argument("--previa", action="store_true", help="mostra o que seria enviado, sem enviar nem marcar")
    al.add_argument("--teste", action="store_true", help="envia uma mensagem de teste pelos canais configurados")

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
    elif a.cmd == "acompanhar":
        stats = Coletor(cfg, regras, con).acompanhar(somente_candidatas=a.candidatas)
        print(json.dumps(stats, ensure_ascii=False))
    elif a.cmd == "resultados":
        lista = relatorio.buscar(con, a.numero) if a.numero else relatorio.candidatas(con)
        print(relatorio.texto_resultados(con, lista))
        print(f"Gravado: {relatorio.gravar_itens_csv(con, lista, cfg.saida_dir)}")
    elif a.cmd == "eventos":
        print(relatorio.texto_eventos(relatorio.eventos(con, a.desde, a.candidatas)))
    elif a.cmd in ("viabilidade", "ficha"):
        from . import viabilidade as vb_mod
        premissas = vb_mod.carregar_premissas(cfg.premissas_path)
        cotacoes = vb_mod.carregar_cotacoes(cfg.cotacoes_path)
        if a.cmd == "viabilidade":
            lista = vb_mod.fichas(con, premissas, cotacoes)
            if a.abertas:
                lista = [f for f in lista if f["criterios"]["prazo"]]
            print(vb_mod.texto_lista(lista))
            print(f"Gravado: {vb_mod.gravar_csv(lista, cfg.saida_dir)}")
        else:
            achadas = relatorio.buscar(con, a.numero)
            if not achadas:
                print(f"{a.numero}: nao encontrado no banco")
            for c in achadas:
                for f in vb_mod.fichas(con, premissas, cotacoes, c["numero_controle"]):
                    if a.item is None or f["item"]["numero_item"] == a.item:
                        print(vb_mod.texto_ficha(f) + "\n")
    elif a.cmd == "simular":
        from . import simulador, viabilidade as vb_mod
        base = vb_mod.carregar_premissas(cfg.premissas_path)
        cenarios = simulador.carregar_cenarios(cfg.premissas_path, base)
        cotacoes = vb_mod.carregar_cotacoes(cfg.cotacoes_path)
        alvo = relatorio.buscar(con, a.numero) if a.numero else [None]
        lista = []
        for c in alvo:
            lista += simulador.simular(con, cenarios, cotacoes, c["numero_controle"] if c else None, a.item)
        print(simulador.texto(lista))
        print(f"Gravado: {simulador.gravar_csv(lista, cfg.saida_dir)}")
    elif a.cmd == "alertas":
        return _alertas(cfg, con, a)
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


def _alertas(cfg, con, a) -> int:
    import yaml
    from . import alertas, simulador, viabilidade as vb_mod
    canais = alertas.canais_configurados()
    if a.teste:
        if not canais:
            print("Nenhum canal configurado (SMTP_HOST + ALERT_EMAIL_TO, ou TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).")
            return 1
        teste = [{"tipo": "situacao", "titulo": "Teste", "detalhe": "Mensagem de teste do monitor de licitações.",
                  "url": None, "chave": "teste", "criado_em": ""}]
        ok = alertas.enviar(con, teste, canais=canais)
        print(f"Teste enviado por: {', '.join(ok) or 'nenhum canal (veja o log)'}")
        return 0 if ok else 1
    prem = yaml.safe_load(cfg.premissas_path.read_text(encoding="utf-8")) or {}
    horas = float((prem.get("alertas") or {}).get("prazo_horas", 48))
    cotacoes = vb_mod.carregar_cotacoes(cfg.cotacoes_path)
    if a.previa:
        # Previa numa copia do banco em memoria: nada e gravado no banco real.
        import sqlite3
        copia = sqlite3.connect(":memory:")
        con.backup(copia)
        copia.row_factory = sqlite3.Row
        con = copia
    novos = alertas.gerar(con, cotacoes, horas)
    lista = alertas.pendentes(con)
    # Cenarios dos itens cotados que aparecem nos alertas.
    base = vb_mod.carregar_premissas(cfg.premissas_path)
    cenarios = simulador.carregar_cenarios(cfg.premissas_path, base)
    citados = {a_["numero_controle"] for a_ in lista if a_.get("numero_controle")}
    sim = [s_ for nc in citados for s_ in simulador.simular(con, cenarios, cotacoes, nc)]
    print(alertas.assunto(lista))
    print(alertas.texto(lista))
    if a.previa:
        print(f"\n(previa: {len(lista)} alerta(s), nada enviado; canais configurados: {', '.join(canais) or 'nenhum'})")
        return 0
    ok = alertas.enviar(con, lista, simulador.html(sim))
    print(f"\n{novos} alerta(s) novo(s), {len(lista)} pendente(s); enviado por: "
          f"{', '.join(ok) if ok else ('nada a enviar' if not lista else 'nenhum canal')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

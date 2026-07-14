"""Enriquecimento: casos ONCOLOGICOS no SUS por municipio (Paineis CONASEMS / DATASUS).

Cruza a judicializacao oncologica (por municipio) com a producao/registro de casos
oncologicos no SUS do mesmo municipio, dando contexto de DEMANDA REAL:
  - muitos casos SUS + muita judicializacao  -> gargalo de acesso (mercado prioritario)
  - poucos casos SUS + muita judicializacao  -> oferta local insuficiente (habilitacao)

Fonte: POST https://paineis.conasems.org.br/assets/ajax/producao.php?option=oncologiaCasos
        body: ibge=<IBGE6>&tipo=cit   (IBGE de 6 digitos; o 7o e digito verificador)
Retorno: [{ibge, municipio, diagnostico:"C50 - ...", casos, referencia:"AAAA-01-01"} ...]

IMPORTANTE:
 - IBGE do DataJud vem com 7 digitos (ex. 3550308); a API CONASEMS usa 6 (355030).
   Use ibge6() para converter.
 - A API NAO tem CORS aberto -> coleta server-side e cache em disco (dashboard embute).
 - Sem chave/segredo: dado publico agregado, sem PII.

Uso:
  python -m src.enrich.conasems_onco            # le municipios do monitor.db e atualiza o cache
  python -m src.enrich.conasems_onco 355030 ... # lista explicita de IBGE6
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

BASE = "https://paineis.conasems.org.br"
_ROOT = Path(__file__).resolve().parents[2]
CACHE = _ROOT / "cache" / "conasems_onco.json"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def ibge6(codigo: object) -> Optional[str]:
    """Normaliza um IBGE (6 ou 7 digitos) para 6 digitos (sem o verificador)."""
    s = "".join(ch for ch in str(codigo or "") if ch.isdigit())
    if len(s) >= 6:
        return s[:6]
    return None


def _post_onco(ibge: str, tries: int = 2) -> Optional[list]:
    body = urllib.parse.urlencode({"ibge": str(ibge), "tipo": "cit"}).encode()
    url = f"{BASE}/assets/ajax/producao.php?option=oncologiaCasos"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"User-Agent": "monitor-jud-g3/1.0"})
            raw = urllib.request.urlopen(req, timeout=20, context=_CTX).read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else None
        except Exception as e:  # noqa
            last = e
            time.sleep(0.8 * (i + 1))
    print(f"  ! falha oncologiaCasos ibge={ibge}: {last}", file=sys.stderr)
    return None


def _agg(rows: list) -> Optional[dict]:
    if not rows:
        return None
    por_ano: Dict[str, Dict[str, int]] = {}
    nome = None
    for x in rows:
        nome = nome or x.get("municipio")
        ref = str(x.get("referencia", ""))
        ano = ref[:4] if ref else "?"
        diag = x.get("diagnostico", "?")
        try:
            casos = int(float(str(x.get("casos", 0)).replace(",", ".")))
        except Exception:
            casos = 0
        por_ano.setdefault(ano, {})
        por_ano[ano][diag] = por_ano[ano].get(diag, 0) + casos
    anos = sorted(a for a in por_ano if a.isdigit())
    ult = anos[-1] if anos else None
    total_ult = sum(por_ano.get(ult, {}).values()) if ult else 0
    top = sorted(por_ano.get(ult, {}).items(), key=lambda kv: kv[1], reverse=True)[:10] if ult else []
    return {
        "municipio": nome,
        "por_ano": por_ano,
        "ult_ano": ult,
        "total_ult_ano": total_ult,
        "top_cids_ult": top,
    }


def fetch_oncologia(ibge_qualquer: object) -> Optional[dict]:
    """Busca (ao vivo) os casos oncologicos SUS de um municipio. Aceita IBGE 6 ou 7 digitos."""
    i6 = ibge6(ibge_qualquer)
    if not i6:
        return None
    return _agg(_post_onco(i6))


def _salvar(out: dict) -> None:
    CACHE.parent.mkdir(exist_ok=True)
    tmp = CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


def coletar(ibges: Iterable[object], max_por_run: Optional[int] = None,
            revisar: bool = False) -> dict:
    """Coleta oncologia SUS p/ varios municipios; RESUMIVEL e incremental.

    - Reaproveita o cache existente (nao refaz o que ja tem, salvo revisar=True).
    - Salva a cada 15 municipios (e no fim) -> interrupcao nao perde o progresso.
    - max_por_run limita quantos NOVOS municipios coletar nesta execucao (resto fica
      p/ a proxima run; util no CI). None = sem limite.
    Retorna o dict {meta, mun:{ibge6:{...}}}.
    """
    vistos: List[str] = []
    for c in ibges:
        i6 = ibge6(c)
        if i6 and i6 not in vistos:
            vistos.append(i6)

    out = carregar_cache()
    out.setdefault("mun", {})
    out["meta"] = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "fonte": "Painéis CONASEMS (paineis.conasems.org.br) — Painel Oncologia / DATASUS",
        "endpoint": "producao.php?option=oncologiaCasos (tipo=cit)",
    }

    pendentes = [i for i in vistos if revisar or i not in out["mun"]]
    if max_por_run:
        pendentes = pendentes[:max_por_run]
    ja = len(vistos) - len(pendentes)
    print(f"  {len(vistos)} municipios alvo · {ja} já em cache · coletando {len(pendentes)}…")

    for k, i6 in enumerate(pendentes, 1):
        rec = _agg(_post_onco(i6))
        if rec:
            out["mun"][i6] = rec
        print(f"  [{k}/{len(pendentes)}] {i6}: {'ok' if rec else '—'}"
              f"{' ('+str(rec['total_ult_ano'])+' casos '+str(rec['ult_ano'])+')' if rec else ''}")
        if k % 15 == 0:
            _salvar(out)
        time.sleep(0.15)

    _salvar(out)
    print(f"==> {len(out['mun'])} municipios no cache ({CACHE})")
    return out


def carregar_cache() -> dict:
    """Le o cache (para o build do dashboard). {} se ausente."""
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {"meta": {}, "mun": {}}


def _ibges_do_banco() -> List[str]:
    """IBGE6 distintos dos municipios COM acao ONCOLOGICA no monitor.db.

    So esses entram no painel de cruzamento, entao coletar so eles mantem a
    coleta enxuta (e o CI dentro do tempo).
    """
    from ..store.db import get_session  # import tardio para nao acoplar
    from ..store.models import Processo
    from sqlmodel import select
    ibges: List[str] = []
    with get_session() as session:
        rows = session.exec(
            select(Processo.municipio_ibge).where(Processo.oncologico == True)  # noqa: E712
        ).all()
        for codigo in rows:
            i6 = ibge6(codigo)
            if i6 and i6 not in ibges:
                ibges.append(i6)
    return ibges


def main(argv: Optional[List[str]] = None) -> None:
    import os
    argv = argv if argv is not None else sys.argv[1:]
    ibges = [a for a in argv if a.strip().isdigit()]
    if not ibges:
        try:
            ibges = _ibges_do_banco()
        except Exception as e:  # noqa
            print(f"Nao consegui ler o banco ({e}); passe IBGEs por argumento.", file=sys.stderr)
            return
    if not ibges:
        print("Nenhum municipio (com acao oncologica) para coletar.", file=sys.stderr)
        return
    # No CI, limita quantos NOVOS coletar por run (resume nas proximas). Local: sem limite.
    cap_env = os.environ.get("CONASEMS_MAX_POR_RUN", "").strip()
    max_run = int(cap_env) if cap_env.isdigit() and int(cap_env) > 0 else None
    print(f"Coletando oncologia SUS de municipios com acao oncologica (CONASEMS)…")
    coletar(ibges, max_por_run=max_run)


if __name__ == "__main__":
    main()

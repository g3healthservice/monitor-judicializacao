"""Classificacao pelas faixas do Tema 1.234/STF.

Regras (parametros, nao inferencia):
  custo_anual >= 210 SM              -> Justica Federal, Uniao custeia 100%
  7 SM <= custo_anual < 210 SM       -> Justica Estadual, Uniao ressarce 65% (FNS->FES/FMS)
  custo_anual < 7 SM                 -> custeio local, sem ressarcimento federal
  oncologico                         -> Uniao ressarce 80%

Precedencia do oncologico (confirmada com o cliente): 80% sobrepoe a faixa,
mas NUNCA reduz a parte da Uniao abaixo do percentual da propria faixa. Logo:
  - faixa federal (100%)  -> permanece 100% (max(0.80, 1.00))
  - faixa estadual (65%)  -> vira 80%       (max(0.80, 0.65))
  - faixa local (<7 SM)   -> vira 80%       (max(0.80, 0.00))  [ressarcivel via ente estadual]

Os limiares 7 e 210 sao derivados de SALARIO_MINIMO (parametro configuravel).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ..config import constants as C
from ..config.settings import Settings, get_settings


class ResultadoClassificacao(BaseModel):
    faixa: str
    percentual_ressarcivel: float
    justica_competente: str  # FEDERAL | ESTADUAL | LOCAL
    custo_anual_estimado: Optional[float]
    valor_ressarcivel_estimado: Optional[float]
    limiar_estadual_valor: float
    limiar_federal_valor: float


def classificar(
    custo_anual_estimado: Optional[float],
    oncologico: bool = False,
    valor_pago_estimado: Optional[float] = None,
    settings: Optional[Settings] = None,
) -> ResultadoClassificacao:
    """Enquadra um tratamento na faixa do Tema 1.234.

    - custo_anual_estimado: base para a faixa (via valor da causa e/ou CMED).
    - oncologico: aplica a regra dos 80% (com guarda de nao-reducao).
    - valor_pago_estimado: base para o valor ressarcivel; se ausente, usa o
      proprio custo_anual_estimado.
    """
    settings = settings or get_settings()
    lim_est = settings.limiar_estadual_valor
    lim_fed = settings.limiar_federal_valor

    if custo_anual_estimado is None:
        # Sem base de custo nao ha como enquadrar; retorna indefinido conservador.
        return ResultadoClassificacao(
            faixa=C.FAIXA_LOCAL,
            percentual_ressarcivel=0.0,
            justica_competente="INDEFINIDO",
            custo_anual_estimado=None,
            valor_ressarcivel_estimado=None,
            limiar_estadual_valor=lim_est,
            limiar_federal_valor=lim_fed,
        )

    # Faixa base por valor.
    if custo_anual_estimado >= lim_fed:
        faixa = C.FAIXA_FEDERAL
        pct_base = C.PCT_FEDERAL_INTEGRAL
        justica = "FEDERAL"
    elif custo_anual_estimado >= lim_est:
        faixa = C.FAIXA_ESTADUAL
        pct_base = C.PCT_ESTADUAL_RESSARCIMENTO
        justica = "ESTADUAL"
    else:
        faixa = C.FAIXA_LOCAL
        pct_base = C.PCT_LOCAL_SEM_RESSARCIMENTO
        justica = "LOCAL"

    percentual = pct_base
    if oncologico:
        percentual = max(pct_base, C.PCT_ONCOLOGICO)
        # Oncologico com custo abaixo do limiar estadual torna-se ressarcivel
        # pelo ente estadual (mecanismo FNS->FES/FMS), deixa de ser "LOCAL puro".
        if faixa != C.FAIXA_FEDERAL:
            faixa = C.FAIXA_ONCOLOGICO
            if justica == "LOCAL":
                justica = "ESTADUAL"

    base_valor = valor_pago_estimado if valor_pago_estimado is not None else custo_anual_estimado
    valor_ressarcivel = round(base_valor * percentual, 2)

    return ResultadoClassificacao(
        faixa=faixa,
        percentual_ressarcivel=percentual,
        justica_competente=justica,
        custo_anual_estimado=custo_anual_estimado,
        valor_ressarcivel_estimado=valor_ressarcivel,
        limiar_estadual_valor=lim_est,
        limiar_federal_valor=lim_fed,
    )

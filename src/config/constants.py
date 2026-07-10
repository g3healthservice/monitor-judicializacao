"""Constantes de dominio editaveis.

Regras juridicas do Tema 1.234/STF sao PARAMETROS, nao inferidas em runtime.
"""

# Multiplicadores de salario minimo que definem as faixas do Tema 1.234.
# custo_anual >= LIMIAR_FEDERAL_SM * SM  -> Justica Federal, Uniao 100%
# LIMIAR_ESTADUAL_SM * SM <= custo_anual < LIMIAR_FEDERAL_SM * SM -> Estadual, Uniao ressarce
# custo_anual < LIMIAR_ESTADUAL_SM * SM  -> local, sem ressarcimento federal
LIMIAR_ESTADUAL_SM = 7
LIMIAR_FEDERAL_SM = 210

# Percentuais de custeio/ressarcimento pela Uniao.
PCT_FEDERAL_INTEGRAL = 1.00       # faixa federal: Uniao custeia 100%
PCT_ESTADUAL_RESSARCIMENTO = 0.65  # faixa estadual: Uniao ressarce 65% (FNS->FES/FMS)
PCT_ONCOLOGICO = 0.80              # oncologicos: Uniao ressarce 80%
PCT_LOCAL_SEM_RESSARCIMENTO = 0.00

# Rotulos de faixa (persistidos e usados em relatorios).
FAIXA_FEDERAL = "FEDERAL_100"
FAIXA_ESTADUAL = "ESTADUAL_65"
FAIXA_ONCOLOGICO = "ONCOLOGICO_80"
FAIXA_LOCAL = "LOCAL_SEM_RESSARCIMENTO"

# Assuntos de saude (codigos TPU/CNJ) usados no Painel da Saude do CNJ.
# Lista editavel: ajuste conforme evolucao da TPU.
ASSUNTOS_SAUDE = [
    10064, 10065, 10066, 10067, 10069, 10070, 10071,
    11851, 11852, 11853, 11854, 11855, 11856, 11857,
    11883, 11884,
    12481, 12483, 12484, 12485,
    12491, 12492, 12493, 12494, 12495, 12496, 12497, 12498, 12499, 12500,
    12501, 12502, 12503, 12504, 12505, 12506,
    12511, 12512, 12513, 12514, 12515, 12516, 12517, 12518, 12519,
]

# Capitulo II da CID-10 (neoplasias): C00-C97 e D00-D48.
# Usado como heuristica v1 para marcar oncologico quando ha CID no objeto.
CID_ONCOLOGICO_PREFIXOS = tuple(
    [f"C{n:02d}" for n in range(0, 98)] + [f"D{n:02d}" for n in range(0, 49)]
)

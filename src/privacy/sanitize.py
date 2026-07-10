"""Fronteira LGPD: sanitiza um hit bruto do DataJud ANTES de persistir.

Politica (whitelist): apenas metadados processuais publicos passam. Qualquer
campo fora da whitelist e descartado por construcao. Adicionalmente, uma
varredura por nomes de campo tipicamente sensiveis registra/alerta caso a API
passe a devolver PII, para falharmos de forma visivel em vez de vazar.

A base publica do DataJud (CNJ) e, por design, metadata-only (nao traz partes,
CPF, nem dados clinicos de pessoa identificavel). Este modulo e defesa em
profundidade: garante que, mesmo se a resposta mudar, nada identificavel e
gravado.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Campos de topo permitidos no _source de um processo.
_ALLOWED_TOP = {
    "numeroProcesso",
    "tribunal",
    "grau",
    "classe",
    "assuntos",
    "orgaoJulgador",
    "dataAjuizamento",
    "dataHoraUltimaAtualizacao",
    "sistema",
    "formato",
    "movimentos",
    "valorCausa",  # opcional; raramente presente na base publica
    "nivelSigilo",
}

# Subcampos permitidos em estruturas aninhadas.
_ALLOWED_CLASSE = {"codigo", "nome"}
_ALLOWED_ASSUNTO = {"codigo", "nome"}
_ALLOWED_ORGAO = {"codigo", "nome", "codigoMunicipioIBGE"}
_ALLOWED_MOVIMENTO = {"codigo", "nome", "dataHora"}

# Tokens que denunciam PII. Se aparecerem como chave em qualquer nivel,
# consideramos violacao de politica (o campo e descartado e sinalizado).
_PII_TOKENS = (
    "nome",  # tratado com cuidado abaixo (permitido apenas em classe/assunto/orgao/movimento)
    "parte",
    "cpf",
    "cnpj",
    "documento",
    "advogado",
    "polo",
    "pessoa",
    "endereco",
    "email",
    "telefone",
    "nascimento",
    "genitor",
    "paciente",
)


def _sanitize_lista(itens: Any, permitidos: set) -> List[Dict[str, Any]]:
    out = []
    if not isinstance(itens, list):
        return out
    for it in itens:
        if isinstance(it, dict):
            out.append({k: v for k, v in it.items() if k in permitidos})
    return out


def detectar_pii(source: Dict[str, Any]) -> List[str]:
    """Retorna caminhos de chaves suspeitas de PII no hit bruto (fora da whitelist).

    'nome' e ignorado quando aparece dentro de estruturas onde e legitimo
    (classe/assunto/orgaoJulgador/movimento designam entidades, nao pessoas).
    """
    suspeitas: List[str] = []
    # Estruturas onde 'nome' designa uma entidade (nao pessoa): classe/assunto/
    # orgao/movimento e metadados tecnicos (sistema=PJe, formato=Eletronico).
    campos_nome_ok = {"classe", "assuntos", "orgaoJulgador", "movimentos", "sistema", "formato"}

    def _walk(obj: Any, caminho: str, dentro_de_estrutura_ok: bool) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                novo_caminho = f"{caminho}.{k}" if caminho else k
                for token in _PII_TOKENS:
                    if token in kl:
                        if token == "nome" and dentro_de_estrutura_ok:
                            continue
                        suspeitas.append(novo_caminho)
                        break
                prox_ok = dentro_de_estrutura_ok or k in campos_nome_ok
                _walk(v, novo_caminho, prox_ok)
        elif isinstance(obj, list):
            for i, it in enumerate(obj):
                _walk(it, f"{caminho}[{i}]", dentro_de_estrutura_ok)

    _walk(source, "", False)
    return suspeitas


def sanitize(source: Dict[str, Any]) -> Dict[str, Any]:
    """Retorna um dict limpo, contendo apenas metadados processuais publicos."""
    if not isinstance(source, dict):
        return {}

    limpo: Dict[str, Any] = {}
    for k, v in source.items():
        if k not in _ALLOWED_TOP:
            continue
        if k == "classe" and isinstance(v, dict):
            limpo[k] = {sk: sv for sk, sv in v.items() if sk in _ALLOWED_CLASSE}
        elif k == "assuntos":
            limpo[k] = _sanitize_lista(v, _ALLOWED_ASSUNTO)
        elif k == "orgaoJulgador" and isinstance(v, dict):
            limpo[k] = {sk: sv for sk, sv in v.items() if sk in _ALLOWED_ORGAO}
        elif k == "movimentos":
            limpo[k] = _sanitize_lista(v, _ALLOWED_MOVIMENTO)
        else:
            limpo[k] = v
    return limpo

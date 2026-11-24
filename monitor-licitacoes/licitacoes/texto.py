"""Leitura de PDF e normalizacao de texto para a busca de sinais."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unicodedata
from typing import List, Tuple

_ESPECIAIS = {"º": "o", "°": "o", "ª": "a", " ": " "}


def normalizar(texto: str) -> str:
    """Minusculas e sem acento, preservando o comprimento.

    Cada caractere vira exatamente um caractere, para que a posicao de um
    achado no texto normalizado aponte o mesmo trecho no texto original.
    """
    saida = []
    for c in texto:
        if c in _ESPECIAIS:
            saida.append(_ESPECIAIS[c])
            continue
        base = unicodedata.normalize("NFD", c)[0].lower()
        saida.append(base[0] if base else c)
    return "".join(saida)


def extrair_pdf(conteudo: bytes) -> Tuple[str, List[int]]:
    """Extrai o texto de um PDF.

    Retorna (texto, inicios) onde inicios[i] e a posicao em que a pagina i+1
    comeca no texto. Usa pdftotext quando instalado (mais fiel) e pypdf caso
    contrario.
    """
    paginas = _pdftotext(conteudo) if shutil.which("pdftotext") else None
    if paginas is None:
        paginas = _pypdf(conteudo)
    inicios, partes, pos = [], [], 0
    for p in paginas:
        inicios.append(pos)
        partes.append(p)
        pos += len(p) + 1
    return "\n".join(partes), inicios


def _pdftotext(conteudo: bytes):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(conteudo)
        f.flush()
        try:
            r = subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", f.name, "-"],
                capture_output=True,
                timeout=180,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace").split("\f")


def _pypdf(conteudo: bytes) -> List[str]:
    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(conteudo))
    return [(pg.extract_text() or "") for pg in leitor.pages]


def pagina_de(pos: int, inicios: List[int]) -> int:
    pagina = 1
    for i, ini in enumerate(inicios):
        if ini <= pos:
            pagina = i + 1
        else:
            break
    return pagina


def trecho(texto: str, ini: int, fim: int, margem: int = 220) -> str:
    """Frase em volta do achado, com espacos compactados."""
    a = max(0, ini - margem)
    b = min(len(texto), fim + margem)
    # Comeca no inicio da linha (preserva a numeracao da clausula, ex.: "9.27.")
    # ou, sem quebra de linha por perto, no inicio da frase.
    corte = texto.rfind("\n", a, ini)
    if corte == -1:
        corte = texto.rfind(". ", a, ini)
    if corte != -1:
        a = corte + 1
    fins = [p for p in (texto.find(". ", fim, b), texto.find(";", fim, b), texto.find("\n\n", fim, b))
            if p != -1]
    fim_frase = min(fins) if fins else -1
    if fim_frase != -1:
        b = fim_frase + 1
    bruto = " ".join(texto[a:b].split())
    prefixo = "..." if a > 0 and corte == -1 else ""
    sufixo = "..." if b < len(texto) and fim_frase == -1 else ""
    return f"{prefixo}{bruto}{sufixo}"

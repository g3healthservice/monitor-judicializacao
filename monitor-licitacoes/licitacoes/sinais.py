"""Regras de sinal: carrega o YAML, busca no texto e calcula a pontuacao."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from .texto import normalizar, pagina_de, trecho


@dataclass
class Sinal:
    id: str
    nome: str
    nivel: str
    peso: int
    padroes: List[re.Pattern]


@dataclass
class Achado:
    sinal_id: str
    nivel: str
    peso: int
    trecho: str
    pagina: Optional[int]
    ocorrencias: int


@dataclass
class Regras:
    sinais: List[Sinal]
    limiar: int
    exige_alto: bool
    peso_alto: int

    def buscar(self, texto: str, inicios: Optional[List[int]] = None) -> List[Achado]:
        """Primeira ocorrencia de cada sinal no texto, com o trecho exato."""
        norm = normalizar(texto)
        achados = []
        for s in self.sinais:
            primeiro = None
            total = 0
            for p in s.padroes:
                for m in p.finditer(norm):
                    total += 1
                    if primeiro is None or m.start() < primeiro[0]:
                        primeiro = (m.start(), m.end())
            if primeiro:
                achados.append(
                    Achado(
                        sinal_id=s.id,
                        nivel=s.nivel,
                        peso=s.peso,
                        trecho=trecho(texto, *primeiro),
                        pagina=pagina_de(primeiro[0], inicios) if inicios else None,
                        ocorrencias=total,
                    )
                )
        return achados

    def pontuar(self, sinal_ids: Iterable[str]) -> Dict[str, object]:
        ids = set(sinal_ids)
        por_id = {s.id: s for s in self.sinais}
        presentes = [por_id[i] for i in ids if i in por_id]
        pontos = sum(s.peso for s in presentes)
        tem_alto = any(s.nivel == "alto" for s in presentes)
        candidata = pontos >= self.limiar and (tem_alto or not self.exige_alto)
        return {"pontuacao": pontos, "tem_alto": tem_alto, "candidata": candidata}


def frase_para_regex(frase: str) -> str:
    """'sem registro' -> regex que tolera espacos/hifens quebrados entre letras."""
    chars = [c for c in normalizar(frase) if not c.isspace()]
    return r"[\s\-]*".join(re.escape(c) for c in chars)


def carregar_regras(caminho: Path) -> Regras:
    dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    pesos = {k: int(v) for k, v in dados.get("pesos", {}).items()}
    sinais = []
    for item in dados["sinais"]:
        nivel = item["peso"]
        padroes = [re.compile(frase_para_regex(f)) for f in item.get("frases", [])]
        padroes += [re.compile(r) for r in item.get("regex", [])]
        sinais.append(Sinal(item["id"], item["nome"], nivel, pesos[nivel], padroes))
    return Regras(
        sinais=sinais,
        limiar=int(dados.get("limiar", 6)),
        exige_alto=bool(dados.get("exige_alto", True)),
        peso_alto=pesos.get("alto", 3),
    )

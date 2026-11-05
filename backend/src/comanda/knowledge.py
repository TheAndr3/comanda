"""Busca BM25 sobre as seções do documento de políticas. Sem dependências externas."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .stemming import stem

POLICY_FILE = Path(__file__).parent / "policies" / "politicas.md"

# fmt: off
STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "um", "uma", "para", "por", "com", "que", "se", "ao", "aos", "mais", "ou", "nao", "sao",
    "ser", "pelo", "pela", "meu", "minha", "eu", "foi", "tem", "como", "qual", "quando",
}
# fmt: on


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _normalize(text: str) -> list[str]:
    """Tokens comparáveis: sem stopwords, reduzidos ao radical e sem acentos.

    O radical é calculado antes de tirar os acentos, porque o stemmer distingue sufixos
    como "ção" e "ável".
    """
    tokens = []
    for word in re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text.lower())):
        if _strip_accents(word) in STOPWORDS:
            continue
        token = _strip_accents(stem(word))
        if len(token) > 1:
            tokens.append(token)
    return tokens


@dataclass(frozen=True)
class Section:
    title: str
    body: str
    tokens: tuple[str, ...]


@lru_cache(maxsize=1)
def load_sections(path: Path = POLICY_FILE) -> tuple[Section, ...]:
    sections: list[Section] = []
    title, lines = None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if title:
                body = "\n".join(lines).strip()
                sections.append(Section(title, body, tuple(_normalize(title + " " + body))))
            title, lines = line[3:].strip(), []
        elif title:
            lines.append(line)
    if title:
        body = "\n".join(lines).strip()
        sections.append(Section(title, body, tuple(_normalize(title + " " + body))))
    return tuple(sections)


def search_policies(query: str, k: int = 2, k1: float = 1.5, b: float = 0.75) -> list[dict]:
    sections = load_sections()
    terms = _normalize(query)
    if not terms or not sections:
        return []
    n = len(sections)
    avg_len = sum(len(s.tokens) for s in sections) / n
    df = Counter(t for s in sections for t in set(s.tokens))
    scored = []
    for s in sections:
        tf = Counter(s.tokens)
        score = 0.0
        for t in terms:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(s.tokens) / avg_len))
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"title": s.title, "text": s.body, "score": round(sc, 3)} for sc, s in scored[:k]]

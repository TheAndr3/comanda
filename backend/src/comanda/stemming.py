"""Redução de palavras ao radical em português, pelo algoritmo Snowball.

Implementação própria do stemmer descrito em
https://snowballstem.org/algorithms/portuguese/stemmer.html, escrita à mão para manter a
busca na Política sem dependências externas, como o BM25 em `knowledge.py`. Recebe uma
palavra em minúsculas, com acentos, e devolve o radical.
"""

from __future__ import annotations

from collections.abc import Iterable

VOWELS = frozenset("aeiouáéíóúâêô")

STANDARD_SUFFIXES = sorted(
    [
        "eza", "ezas", "ico", "ica", "icos", "icas", "ismo", "ismos", "ável", "ível", "ista",
        "istas", "oso", "osa", "osos", "osas", "amento", "amentos", "imento", "imentos", "adora",
        "ador", "aça~o", "adoras", "adores", "aço~es", "ante", "antes", "ância",
        "logia", "logias", "uça~o", "uço~es", "ência", "ências", "amente", "mente",
        "idade", "idades", "iva", "ivo", "ivas", "ivos", "ira", "iras",
    ],
    key=len,
    reverse=True,
)  # fmt: skip

VERB_SUFFIXES = sorted(
    [
        "ada", "ida", "ia", "aria", "eria", "iria", "ará", "ara", "erá", "era", "irá", "ava",
        "asse", "esse", "isse", "aste", "este", "iste", "ei", "arei", "erei", "irei", "am",
        "iam", "ariam", "eriam", "iriam", "aram", "eram", "iram", "avam", "em", "arem", "erem",
        "irem", "assem", "essem", "issem", "ado", "ido", "ando", "endo", "indo", "ara~o",
        "era~o", "ira~o", "ar", "er", "ir", "as", "adas", "idas", "ias", "arias", "erias",
        "irias", "arás", "aras", "erás", "eras", "irás", "avas", "es", "ardes", "erdes",
        "irdes", "ares", "eres", "ires", "asses", "esses", "isses", "astes", "estes", "istes",
        "is", "ais", "eis", "íeis", "aríeis", "eríeis", "iríeis", "áreis", "areis", "éreis",
        "ereis", "íreis", "ireis", "ásseis", "ésseis", "ísseis", "áveis", "ados", "idos",
        "ámos", "amos", "íamos", "aríamos", "eríamos", "iríamos", "áramos", "éramos", "íramos",
        "ávamos", "emos", "aremos", "eremos", "iremos", "ássemos", "êssemos", "íssemos", "imos",
        "armos", "ermos", "irmos", "eu", "iu", "ou", "ira", "iras",
    ],
    key=len,
    reverse=True,
)  # fmt: skip

RESIDUAL_SUFFIXES = ("os", "a", "i", "o", "á", "í", "ó")


def _region_after_vowel_consonant(word: str, start: int) -> int:
    """Início da região após a primeira consoante que segue uma vogal (R1/R2 do Snowball)."""
    for i in range(start + 1, len(word)):
        if word[i] not in VOWELS and word[i - 1] in VOWELS:
            return i + 1
    return len(word)


def _rv(word: str) -> int:
    """Início da região RV do Snowball, onde ficam os sufixos verbais e residuais."""
    if len(word) < 2:
        return len(word)
    if word[1] not in VOWELS:
        return next((i + 1 for i in range(2, len(word)) if word[i] in VOWELS), len(word))
    if word[0] in VOWELS:
        return next((i + 1 for i in range(2, len(word)) if word[i] not in VOWELS), len(word))
    return min(3, len(word))


def _matching_ending(word: str, endings: Iterable[str]) -> str | None:
    """A primeira terminação de `endings` que `word` tem; as listas vêm da mais longa à mais curta."""
    return next((e for e in endings if word.endswith(e)), None)


def _drop(word: str, endings: Iterable[str], region: int) -> str:
    """Tira de `word` a terminação de `endings` que ele tem, se ela começa dentro da região."""
    ending = _matching_ending(word, endings)
    if ending is None or len(word) - len(ending) < region:
        return word
    return word[: len(word) - len(ending)]


def _standard_suffix(word: str, r1: int, r2: int, rv: int) -> str:
    suffix = _matching_ending(word, STANDARD_SUFFIXES)
    if suffix is None:
        return word
    base = word[: len(word) - len(suffix)]
    start = len(base)

    if suffix in ("logia", "logias"):
        return base + "log" if start >= r2 else word
    if suffix in ("uça~o", "uço~es"):
        return base + "u" if start >= r2 else word
    if suffix in ("ência", "ências"):
        return base + "ente" if start >= r2 else word
    if suffix in ("ira", "iras"):
        return base + "ir" if start >= rv and base.endswith("e") else word
    if suffix == "amente":
        if start < r1:
            return word
        without_iv = _drop(base, ("iv",), r2)
        if without_iv != base:
            return _drop(without_iv, ("at",), r2)
        return _drop(base, ("os", "ic", "ad"), r2)

    if start < r2:
        return word
    if suffix == "mente":
        return _drop(base, ("ante", "avel", "ível"), r2)
    if suffix in ("idade", "idades"):
        return _drop(base, ("abil", "ic", "iv"), r2)
    if suffix in ("iva", "ivo", "ivas", "ivos"):
        return _drop(base, ("at",), r2)
    return base


def stem(word: str) -> str:
    word = word.replace("ã", "a~").replace("õ", "o~")
    rv = _rv(word)
    r1 = _region_after_vowel_consonant(word, 0)
    r2 = _region_after_vowel_consonant(word, r1)

    changed = _standard_suffix(word, r1, r2, rv)
    if changed == word:
        changed = _drop(word, VERB_SUFFIXES, rv)

    if changed != word:
        word = _drop(changed, ("i",), rv) if changed.endswith("ci") else changed
    else:
        word = _drop(word, RESIDUAL_SUFFIXES, rv)

    without_e = _drop(word, ("e", "é", "ê"), rv)
    if without_e != word:
        word = _drop(without_e, ("u", "i"), rv) if without_e.endswith(("gu", "ci")) else without_e
    elif word.endswith("ç"):
        word = word[:-1] + "c"

    return word.replace("a~", "ã").replace("o~", "õ")

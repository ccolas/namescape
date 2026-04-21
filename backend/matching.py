"""Keyword matching: substring / stemmed / regex modes.

All modes normalize Turkish diacritics (ş->s, ı->i, ğ->g, ç->c, ö->o, ü->u,
İ->i) and lowercase before comparing, so users can type keywords with or
without diacritics.

- substring: checks if any keyword (normalized) appears anywhere in the
  normalized name. Fast but misses consonant mutations (kitap -> kitabı).
- stemmed: tokenizes the name, runs Turkish Snowball stemmer on tokens and
  keyword. Matches if keyword stem equals a token stem, or is a prefix of
  one (for extra looseness when the stemmer undersheds).
- regex: user-provided Python regex, case-insensitive, applied to the raw
  (un-normalized) name so patterns with diacritics still work as expected.
"""
import re
import unicodedata
from typing import List

import snowballstemmer

_stemmer = snowballstemmer.stemmer("turkish")

_TR_MAP = str.maketrans({
    "ş": "s", "Ş": "s",
    "ı": "i", "İ": "i",
    "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c",
    "ö": "o", "Ö": "o",
    "ü": "u", "Ü": "u",
})

_token_re = re.compile(r"[A-Za-z0-9]+")


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.translate(_TR_MAP).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(text: str) -> List[str]:
    return _token_re.findall(normalize(text))


def match(name: str, keywords: List[str], mode: str) -> bool:
    kws = [k for k in (keywords or []) if k and k.strip()]
    if not kws:
        return False

    if mode == "substring":
        n = normalize(name)
        return any(normalize(k) in n for k in kws)

    if mode == "stemmed":
        name_stems = [_stemmer.stemWord(t) for t in _tokens(name)]
        if not name_stems:
            return False
        for k in kws:
            ks = _stemmer.stemWord(normalize(k))
            if not ks:
                continue
            for s in name_stems:
                if not s:
                    continue
                if s == ks or s.startswith(ks) or ks.startswith(s):
                    return True
        return False

    if mode == "regex":
        for k in kws:
            try:
                if re.search(k, name, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False

    raise ValueError(f"unknown mode: {mode}")

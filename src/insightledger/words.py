"""Word forms shared by retrieval and reading.

A question asks for "total revenues"; GE prints "Total revenue". Retrieval
and the deterministic reader must both treat those as one word, and by the
same rule, or one finds the page the other then cannot read.
"""
from __future__ import annotations


def singular(word: str) -> str:
    """A lower-case word's singular: revenues -> revenue, liabilities ->
    liability, losses -> loss, taxes -> tax. Words that are not plain plurals
    (status, basis, gross) and short words are left alone."""
    if not word.isalpha() or len(word) <= 3:
        return word
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("ss") or word.endswith(("us", "is")):
        return word
    if word.endswith("es") and word[:-2].endswith(("ss", "x", "ch", "sh")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def stem(word: str) -> str:
    """The part every form of a word shares, for substring matching: both
    "liability" and "liabilities" contain "liabilit"."""
    s = singular(word)
    return s[:-1] if len(s) > 4 and s.endswith("y") and s[-2] not in "aeiou" else s

"""Is each text written in the requested language?

A pipeline asks for its free text (action descriptions, reasoning) in one language and needs to know
when a model drifted into another, so it can retry. Each text gets one verdict:

- `ok`: the detector is confident the text is in the requested language;
- `drift`: the detector is confident the text is not in the requested language, nor in one easily
  confused with it;
- `undecided`: anything the detector cannot settle: low confidence, a language easily confused with
  the requested one (Danish/Norwegian, Spanish/Catalan, Czech/Slovak, ...) or a requested language
  the detector does not know. The caller decides these another way (in the email workflows, an LLM
  step), so this module never guesses.

Detection is py3langid (142 languages, a naive Bayes model shipped with the package, deterministic,
about 0.1 ms per text). Before detection, a text loses what is never a language signal: bracketed
placeholders, URLs, e-mail addresses and tokens with digits (invoice numbers, amounts, dates). Texts
with fewer than `_MIN_WORDS` words left (names, "No AR Action") are not checked; in scripts without
spaces between words, `_MIN_UNSPACED_CHARS` characters count instead.

Output shape: contracts/language.schema.json (schema_version 1.0).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

SCHEMA_VERSION = "1.0"

_MIN_WORDS = 4
# Scripts written without spaces between words (Chinese, Japanese, Thai, Lao, Khmer, Myanmar): there,
# a sentence is one or two "words", so these characters are counted instead.
_UNSPACED = re.compile(r"[぀-ヿ㐀-䶿一-鿿฀-໿က-႟ក-៿]")
_MIN_UNSPACED_CHARS = 8
# Minimum probability for a verdict either way: that a text is in the requested language (ok) or is not
# (drift); in between it is undecided. Every recorded English answer had P(en) >= 0.97 (benchmark of
# 2026-10-28 on 176 recorded answers).
_MIN_CONFIDENCE = 0.9
# Languages the detector confuses with each other; a guess inside the requested language's group is
# never a drift, only undecided.
_CONFUSABLE = [
    {"no", "nn", "da"}, {"es", "ca", "gl", "an", "ext"}, {"pt", "gl"}, {"cs", "sk"},
    {"hr", "sr", "bs", "sl"}, {"id", "ms", "jv"}, {"ru", "uk", "be", "bg"}, {"nl", "af", "fy"},
    {"de", "lb"}, {"fr", "oc", "wa"}, {"it", "lij", "vec"}, {"zh", "yue", "wuu"},
]
# ISO 639-1 codes a caller may send that the model labels differently.
_ALIASES = {"nb": "no", "iw": "he", "in": "id", "zh-cn": "zh", "zh-tw": "zh"}
_PLACEHOLDER = re.compile(r"\[[^\]\n]{0,200}\]")
# Detection needs a sentence or two; a longer text is cut, which also bounds the work per text.
_MAX_CHARS = 2000
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@lru_cache(maxsize=1)
def _identifier() -> Any:
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    return LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)


def supported_languages() -> frozenset[str]:
    return frozenset(_identifier().nb_classes)


def _normalize(code: str) -> str:
    code = (code or "").strip().casefold().replace("_", "-")
    return _ALIASES.get(code, code.split("-")[0])


def _confusable(a: str, b: str) -> bool:
    return any(a in group and b in group for group in _CONFUSABLE)


def _strip_noise(text: str) -> str:
    """Drop what is never a language signal: bracketed placeholders, URLs, addresses, and tokens with
    digits (invoice numbers, amounts, dates). Token by token, so the work stays linear."""
    text = _PLACEHOLDER.sub(" ", text[:_MAX_CHARS])
    return " ".join(t for t in text.split() if "@" not in t and "://" not in t and not any(c.isdigit() for c in t))


def _group(lang: str) -> set[str]:
    return set().union({lang}, *(g for g in _CONFUSABLE if lang in g))


def detect(text: str) -> tuple[str | None, list[tuple[str, float]]]:
    """The detected language and the full ranking [(language, probability)], or (None, []) when the
    text is too short to judge."""
    cleaned = _strip_noise(text or "")
    if len(_WORD.findall(cleaned)) < _MIN_WORDS and len(_UNSPACED.findall(cleaned)) < _MIN_UNSPACED_CHARS:
        return None, []
    ranking = [(lang, float(p)) for lang, p in _identifier().rank(cleaned)]
    return ranking[0][0], ranking


def check_language(texts: list[Any] | str, expected: str) -> dict[str, Any]:
    """Give every text a verdict against `expected` (ISO 639-1); see the module docstring."""
    want = _normalize(expected)
    known = want in supported_languages()
    items = [texts] if isinstance(texts, str) else list(texts or [])
    issues: list[dict[str, Any]] = []
    undecided: list[dict[str, Any]] = []
    checked = 0
    for i, text in enumerate(items):
        if not isinstance(text, str):
            continue
        found, ranking = detect(text)
        if found is None:
            continue
        checked += 1
        group = _group(want)
        # Probability mass of the requested language and the ones it is confused with, summed over
        # the ranking, so a split between close variants (zh/wuu/yue, no/da) still counts in full.
        wanted = sum(p for lang, p in ranking if lang in group)
        not_wanted = 1.0 - wanted
        if known and found == want and wanted >= _MIN_CONFIDENCE:
            continue
        entry = {"index": i, "expected": want, "detected": found, "confidence": round(not_wanted, 3), "text": text[:300]}
        if known and found not in group and not_wanted >= _MIN_CONFIDENCE:
            issues.append(entry)
        else:
            undecided.append(entry)
    status = "drift" if issues else "undecided" if undecided else "ok"
    return {
        "schema_version": SCHEMA_VERSION,
        "language_status": status,
        "language_ok": status == "ok",
        "language_expected": want,
        "language_checked": checked,
        "language_issues": issues,
        "language_undecided": undecided,
    }

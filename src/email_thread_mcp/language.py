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

A text with a word in a script the requested language does not use (an Arabic word in an English
text) is a drift too, however confident the detector is about the rest; Latin is allowed everywhere.

`detect_language` names the language of one text, such as the incoming email, with the same
thresholds; a reply too short or too unclear to judge is undecided and the caller keeps its default.

Output shapes: contracts/language.schema.json (schema_version 1.1),
contracts/detect_language.schema.json (schema_version 1.0).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

SCHEMA_VERSION = "1.1"

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
# Detector labels that are a dialect or an older form of an ISO 639-1 language; detect_language sums
# their probability into that language, so an Arabic text split between ar, ary and arz still counts.
_ISO_639_1 = {"ary": "ar", "arz": "ar", "uzs": "uz", "hbo": "he", "grc": "el"}
_RANKING_SIZE = 3
# The ISO 639-1 codes, for detect_language's fallback: two letters alone ("xx") are not a language.
_ISO_639_1_CODES = frozenset("""
    aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de
    dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id
    ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu
    lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu
    rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr
    ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu
""".split())

# Scripts by the first word of a letter's Unicode name. Latin is allowed in every language: product
# names, identifiers and English terms are written in it everywhere.
_KNOWN_SCRIPTS = {
    "ARABIC", "ARMENIAN", "BENGALI", "CJK", "CYRILLIC", "DEVANAGARI", "ETHIOPIC", "GEORGIAN", "GREEK",
    "GUJARATI", "GURMUKHI", "HANGUL", "HEBREW", "HIRAGANA", "KANNADA", "KATAKANA", "KHMER", "LAO",
    "MALAYALAM", "MONGOLIAN", "MYANMAR", "ORIYA", "SINHALA", "TAMIL", "TELUGU", "THAI", "TIBETAN",
}
# The scripts of each detector language not written in Latin alone; any other language is Latin.
_LANGUAGE_SCRIPTS = {
    "am": {"ETHIOPIC"}, "ar": {"ARABIC"}, "ary": {"ARABIC"}, "arz": {"ARABIC"}, "fa": {"ARABIC"},
    "ps": {"ARABIC"}, "ur": {"ARABIC"}, "ug": {"ARABIC"}, "sdh": {"ARABIC"}, "uzs": {"ARABIC"},
    "ku": {"ARABIC"}, "as": {"BENGALI"}, "bn": {"BENGALI"}, "dz": {"TIBETAN"}, "el": {"GREEK"},
    "grc": {"GREEK"}, "gu": {"GUJARATI"}, "he": {"HEBREW"}, "hbo": {"HEBREW"}, "hi": {"DEVANAGARI"},
    "mr": {"DEVANAGARI"}, "ne": {"DEVANAGARI"}, "sa": {"DEVANAGARI"}, "gom": {"DEVANAGARI"},
    "hy": {"ARMENIAN"}, "ja": {"CJK", "HIRAGANA", "KATAKANA"}, "ka": {"GEORGIAN"}, "km": {"KHMER"},
    "kn": {"KANNADA"}, "ko": {"HANGUL", "CJK"}, "lo": {"LAO"}, "ml": {"MALAYALAM"}, "my": {"MYANMAR"},
    "or": {"ORIYA"}, "pa": {"GURMUKHI"}, "si": {"SINHALA"}, "ta": {"TAMIL"}, "te": {"TELUGU"},
    "th": {"THAI"}, "zh": {"CJK"}, "yue": {"CJK"}, "wuu": {"CJK"}, "mn": {"CYRILLIC", "MONGOLIAN"},
    **{lang: {"CYRILLIC"} for lang in ("ba", "be", "bg", "kk", "ky", "mk", "ru", "sr", "tg", "tt", "uk", "bs")},
}
# Letters of a foreign script in one word that make a text drift: one stray letter (a Greek mu, a
# math symbol) is not enough, a word is.
_MIN_FOREIGN_LETTERS = 2


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


def detect_language(text: str, fallback: str | None = None) -> dict[str, Any]:
    """The language of one text (an incoming email), or undecided: too short, below the confidence
    `check_language` uses, or a language without an ISO 639-1 code. `resolved_language` is the
    detected language, else `fallback` when it is an ISO 639-1 code, else None. Output shape:
    contracts/detect_language.schema.json (schema_version 1.0)."""
    _, ranking = detect(text if isinstance(text, str) else "")
    totals: dict[str, float] = {}
    for lang, p in ranking:
        code = _ISO_639_1.get(lang, lang)
        totals[code] = totals.get(code, 0.0) + p
    top = sorted(totals.items(), key=lambda item: -item[1])[:_RANKING_SIZE]
    found = top[0][0] if top and top[0][1] >= _MIN_CONFIDENCE and len(top[0][0]) == 2 else None
    default = fallback.strip().casefold() if isinstance(fallback, str) else None
    return {
        "schema_version": "1.0",
        "language": found,
        "status": "detected" if found else "undecided",
        "ranking": [{"language": lang, "probability": round(p, 3)} for lang, p in top],
        # A key of its own: a workflow that merges results into one context keeps it when a later
        # step writes `language`.
        "resolved_language": found or (default if default in _ISO_639_1_CODES else None),
    }


def _script(char: str) -> str | None:
    words = unicodedata.name(char, "").replace("-", " ").split()
    if words[:1] in (["FULLWIDTH"], ["HALFWIDTH"]):
        words = words[1:]
    return words[0] if words and words[0] in _KNOWN_SCRIPTS else None


def _foreign_script(text: str, want: str) -> tuple[str, str] | None:
    """The first word written in a script the requested language does not use, as (script, word)."""
    allowed = _LANGUAGE_SCRIPTS.get(want, set())
    for word in text.split():
        counts: dict[str, int] = {}
        for char in word:
            script = _script(char) if char.isalpha() else None
            if script and script not in allowed:
                counts[script] = counts.get(script, 0) + 1
                if counts[script] >= _MIN_FOREIGN_LETTERS:
                    return script, word
    return None


def _script_language(script: str) -> str | None:
    """The language a foreign script names on its own (Hangul: ko), or None when several languages
    write it (Arabic, Devanagari, Cyrillic): one word is too little to tell them apart."""
    candidates = {_ISO_639_1.get(lang, lang) for lang, scripts in _LANGUAGE_SCRIPTS.items() if script in scripts}
    return candidates.pop() if len(candidates) == 1 else None


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
        foreign = _foreign_script(_strip_noise(text), want) if known else None
        checked += 1
        group = _group(want)
        # Probability mass of the requested language and the ones it is confused with, summed over
        # the ranking, so a split between close variants (zh/wuu/yue, no/da) still counts in full.
        wanted = sum(p for lang, p in ranking if lang in group)
        not_wanted = 1.0 - wanted
        entry = {"index": i, "expected": want, "detected": found, "confidence": round(not_wanted, 3),
                 "text": text[:300], "foreign_script": foreign[0].title() if foreign else None}
        if known and found not in group and not_wanted >= _MIN_CONFIDENCE:
            issues.append(entry)
        elif foreign:
            # A word in another script is drift even when the rest of the text is in the right language.
            issues.append({**entry, "detected": _script_language(foreign[0]), "confidence": 1.0})
        elif known and found == want and wanted >= _MIN_CONFIDENCE:
            continue
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

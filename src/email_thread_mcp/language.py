"""Is each text written in the requested language?

A pipeline asks for its free text (action descriptions, reasoning) in one language and needs to know
when a model drifted into another, so it can retry. Full sentences in the common European languages
are told apart reliably by their function words, so this counts stop words instead of loading a
language model: no dependency, deterministic, microseconds per text.

A text is reported only when another language clearly dominates it: at least `_MIN_HITS` of that
language's stop words and more than the requested language has. Short texts, identifiers, names and
an unsupported requested language are never reported, so a check can cause a needless retry at
worst, never a wrong answer.

Output shape: contracts/language.schema.json (schema_version 1.0).
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "1.0"

# Function words only: articles, pronouns, prepositions, conjunctions, auxiliaries. A word two
# languages spell the same way ("is", "que", "die") is dropped from both below, so it never votes.
_RAW_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("the and of to is are was were be been for with on that this these those it its we you they our your their have has had will would should can could not from by at as an or but which who what when there here".split()),
    "de": frozenset("der die das und ist sind war waren nicht ein eine einen einem einer für mit auf dem den des zu von bei wir sie ihr ihre unser unsere wird werden wurde kann können bitte noch auch nach über oder aber dass".split()),
    "fr": frozenset("le la les et est sont était un une des du pour avec sur dans ce cette ces nous vous ils leur leurs pas ne qui que mais ou au aux été sera peut être votre notre".split()),
    "it": frozenset("il lo gli le e è sono era un una per con su nel nella della delle degli che non ma o al alla alle stato sarà può essere vostro nostro questa questo".split()),
    "es": frozenset("el los las y es son era un una para con por del al que no pero o su sus fue será puede ser nuestro nuestra vuestro esta este estos estas como".split()),
    "nl": frozenset("het een en is zijn was waren voor met op dat deze die niet maar of naar door wij jullie hun ons onze wordt worden kan kunnen ook nog bij".split()),
    "pt": frozenset("o os as e é são era um uma para com por do da dos das ao que não mas ou seu sua foi será pode ser nosso nossa este esta".split()),
    "pl": frozenset("i jest są był była nie się na w z do że to ten ta te dla przez od po ale lub jak który która które nasz nasza wasz".split()),
}
_SHARED = {w for lang, ws in _RAW_STOPWORDS.items() for w in ws
           if any(w in other for o, other in _RAW_STOPWORDS.items() if o != lang)}
_STOPWORDS: dict[str, frozenset[str]] = {lang: ws - _SHARED for lang, ws in _RAW_STOPWORDS.items()}
_MIN_WORDS = 5
_MIN_HITS = 3
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)


def detect(text: str) -> tuple[str | None, dict[str, int]]:
    """The dominant language of `text` by stop-word hits, or None when the text is too short or no
    language reaches `_MIN_HITS`. Also returns the hit count per language."""
    words = [w.casefold() for w in _WORD.findall(text or "")]
    if len(words) < _MIN_WORDS:
        return None, {}
    hits = {lang: sum(w in sw for w in words) for lang, sw in _STOPWORDS.items()}
    best = max(hits, key=lambda k: hits[k])
    return (best if hits[best] >= _MIN_HITS else None), hits


def check_language(texts: list[Any] | str, expected: str) -> dict[str, Any]:
    """Report every text clearly written in another language than `expected` (ISO 639-1)."""
    want = (expected or "").strip().casefold()
    items = [texts] if isinstance(texts, str) else list(texts or [])
    issues: list[dict[str, Any]] = []
    checked = 0
    if want in _STOPWORDS:
        for i, text in enumerate(items):
            if not isinstance(text, str):
                continue
            found, hits = detect(text)
            if found is None:
                continue
            checked += 1
            if found != want and hits[found] > hits[want]:
                issues.append({"index": i, "expected": want, "detected": found, "text": text[:200]})
    return {
        "schema_version": SCHEMA_VERSION,
        "language_ok": not issues,
        "expected": want,
        "checked": checked,
        "language_issues": issues,
    }

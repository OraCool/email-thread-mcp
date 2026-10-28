import pytest

from email_thread_mcp.language import _STOPWORDS, check_language, detect
from email_thread_mcp.server import check_language as check_language_tool

# Free text the email pipeline actually returned with language "en" (Langfuse, 2026-10-27/28).
DRIFTED = {
    "de": "Sende den unterschriebenen Liefernachweis für Lieferschein 7781-B, damit die Sperre für INV-2026-10478 aufgehoben werden kann.",
    "es": "No se requiere ninguna acción de cuentas por cobrar; el correo es un boletín informativo general sin impacto en cobros.",
    "it": "Non ci sono azioni di incasso da intraprendere, perché la email è una newsletter generale senza richieste.",
    "fr": "Le message contient uniquement des informations générales et n’exige aucune action de recouvrement pour nous.",
}
ENGLISH = [
    "Send the signed proof of delivery for delivery note 7781-B so the customer can post the goods receipt.",
    "Classified as 38 (a disagreement over the billed rate) from the latest message alone: the sender says the three invoices are on hold.",
    "Follow up after 9 November on INV-2026-10452 and ask for the date the invoice will be paid.",
]


def test_no_word_votes_for_two_languages() -> None:
    langs = list(_STOPWORDS)
    for i, a in enumerate(langs):
        for b in langs[i + 1:]:
            assert not (_STOPWORDS[a] & _STOPWORDS[b]), (a, b, _STOPWORDS[a] & _STOPWORDS[b])


@pytest.mark.parametrize("lang,text", list(DRIFTED.items()))
def test_drifted_text_is_detected(lang, text) -> None:
    assert detect(text)[0] == lang


@pytest.mark.parametrize("text", ENGLISH)
def test_english_text_is_english(text) -> None:
    assert detect(text)[0] == "en"


def test_drift_is_reported_with_its_index() -> None:
    out = check_language([ENGLISH[0], DRIFTED["de"], ENGLISH[1]], "EN")
    assert not out["language_ok"]
    assert [(i["index"], i["detected"]) for i in out["language_issues"]] == [(1, "de")]
    assert out["checked"] == 3 and out["expected"] == "en"


def test_all_english_is_ok() -> None:
    out = check_language(ENGLISH, "en")
    assert out["language_ok"] and out["language_issues"] == []


def test_requested_german_accepts_german_and_reports_english() -> None:
    out = check_language([DRIFTED["de"], ENGLISH[0]], "de")
    assert [i["index"] for i in out["language_issues"]] == [1]


@pytest.mark.parametrize("text", ["No AR Action", "INV-2026-10452, INV-2026-10478", "Brenner Maschinenbau AG", "", "EUR 24,770.50"])
def test_short_text_names_and_identifiers_are_never_reported(text) -> None:
    assert check_language([text], "en")["language_ok"]


def test_an_english_sentence_naming_a_german_company_stays_english() -> None:
    text = "The customer Brenner Maschinenbau AG says the plant controller is on leave and the invoice will be paid later."
    assert check_language([text], "en")["language_ok"]


def test_unsupported_expected_language_never_reports() -> None:
    out = check_language([DRIFTED["de"]], "sv")
    assert out["language_ok"] and out["checked"] == 0


def test_non_strings_are_skipped() -> None:
    assert check_language([None, 42, {"x": 1}, DRIFTED["es"]], "en")["language_issues"][0]["index"] == 3


def test_tool_accepts_texts_as_json_text() -> None:
    import json

    out = check_language_tool(json.dumps([ENGLISH[0], DRIFTED["fr"]]), "en")
    assert [i["detected"] for i in out["language_issues"]] == ["fr"]

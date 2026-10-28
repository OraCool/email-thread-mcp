import json

import pytest

from email_thread_mcp.language import check_language, detect, supported_languages
from email_thread_mcp.server import check_language as check_language_tool

# Synthetic texts shaped like the action descriptions and reasoning an AR email pipeline returns.
ENGLISH = [
    "Send the signed proof of delivery for delivery note 1234-A so the customer can post the goods receipt.",
    "Classified as 38 (a disagreement over the billed rate) from the latest message alone: the sender says the three invoices are on hold.",
    "Follow up after 9 November on INV-0001 and ask for the date the invoice will be paid.",
    "The customer Muster Maschinenbau AG says the plant controller is on leave and the invoice will be paid later.",
]
# The same kind of sentence in another language, as a drifting model writes it.
DRIFTED = {
    "de": "Sende den unterschriebenen Liefernachweis für den Lieferschein, damit die Sperre aufgehoben werden kann.",
    "es": "Enviar la prueba de entrega firmada para el albarán, de modo que el cliente pueda registrar la entrada.",
    "it": "Non ci sono azioni di incasso da intraprendere, perché la email è una newsletter generale senza richieste.",
    "fr": "Le message contient uniquement des informations générales et n'exige aucune action de recouvrement.",
    "pl": "Klient twierdzi, że faktura zostanie opłacona w przyszłym tygodniu, ponieważ kontroler jest na urlopie.",
    "sv": "Kunden säger att fakturan kommer att betalas nästa vecka eftersom controllern är på semester.",
    "fi": "Asiakas kertoo, että lasku maksetaan ensi viikolla, koska kontrolleri on lomalla.",
    "ja": "お客様によると、担当者が休暇中のため、請求書は来週支払われる予定です。",
    "zh": "客户表示，由于主管正在休假，发票将在下周支付。",
}


def test_many_languages_are_supported() -> None:
    assert {"en", "de", "fr", "it", "es", "pt", "nl", "pl", "sv", "da", "no", "fi", "cs", "ja", "zh"} <= supported_languages()


@pytest.mark.parametrize("lang,text", list(DRIFTED.items()))
def test_drifted_text_is_detected(lang, text) -> None:
    assert detect(text)[0] == lang


@pytest.mark.parametrize("lang,text", list(DRIFTED.items()))
def test_drift_against_english_is_reported(lang, text) -> None:
    out = check_language([ENGLISH[0], text], "EN")
    assert out["language_status"] == "drift" and not out["language_ok"]
    assert [(i["index"], i["detected"]) for i in out["language_issues"]] == [(1, lang)]


def test_all_english_is_ok() -> None:
    out = check_language(ENGLISH, "en")
    assert out["language_status"] == "ok" and out["language_ok"]
    assert out["language_issues"] == [] and out["language_undecided"] == []
    assert out["language_checked"] == len(ENGLISH) and out["language_expected"] == "en"


@pytest.mark.parametrize("lang,text", list(DRIFTED.items()))
def test_each_language_accepts_itself(lang, text) -> None:
    assert check_language([text], lang)["language_status"] == "ok"


def test_requested_german_reports_english() -> None:
    out = check_language([DRIFTED["de"], ENGLISH[0]], "de")
    assert [i["index"] for i in out["language_issues"]] == [1]


@pytest.mark.parametrize("text", ["No AR Action", "请求", "Muster Maschinenbau AG", "INV-0001, INV-0002", "Muster Maschinenbau", "", "EUR 24,770.50", "[Invoice PDF attached]"])
def test_short_text_names_and_identifiers_are_not_checked(text) -> None:
    out = check_language([text], "en")
    assert out["language_status"] == "ok" and out["language_checked"] == 0


def test_a_confusable_language_is_undecided_not_drift() -> None:
    danish = "Kunden siger, at fakturaen bliver betalt i næste uge, fordi controlleren er på ferie."
    out = check_language([danish], "nb")  # nb is the model's "no"
    assert out["language_expected"] == "no"
    assert out["language_status"] in ("ok", "undecided") and out["language_issues"] == []


def test_a_short_foreign_phrase_is_a_drift() -> None:
    out = check_language(["Enviar la factura corregida hoy."], "en")
    assert out["language_status"] == "drift"


def test_a_sentence_mixing_two_languages_is_undecided() -> None:
    out = check_language(["The Rechnung wird nächste Woche bezahlt, as agreed with the controller."], "en")
    assert out["language_status"] == "undecided" and out["language_issues"] == []
    assert out["language_undecided"][0]["index"] == 0


def test_unknown_requested_language_leaves_everything_undecided() -> None:
    out = check_language([ENGLISH[0], DRIFTED["de"]], "xx")
    assert out["language_status"] == "undecided" and out["language_issues"] == []
    assert [u["index"] for u in out["language_undecided"]] == [0, 1]


def test_drift_wins_over_undecided() -> None:
    out = check_language(["The Rechnung wird nächste Woche bezahlt, as agreed with the controller.", DRIFTED["de"]], "en")
    assert out["language_status"] == "drift"
    assert len(out["language_issues"]) == 1 and len(out["language_undecided"]) == 1


def test_non_strings_are_skipped() -> None:
    assert check_language([None, 42, {"x": 1}, DRIFTED["es"]], "en")["language_issues"][0]["index"] == 3


def test_tool_accepts_texts_as_json_text() -> None:
    out = check_language_tool(json.dumps([ENGLISH[0], DRIFTED["fr"]]), "en")
    assert [i["detected"] for i in out["language_issues"]] == ["fr"]


def test_long_text_without_spaces_is_fast() -> None:
    import time

    start = time.perf_counter()
    check_language(["a" * 200_000, "[" * 100_000, ("x" * 50_000) + "@"], "en")
    assert time.perf_counter() - start < 1.0

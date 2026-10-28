"""0.9.0: the draft is written in the incoming email's language. Invented texts only."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError

from email_thread_mcp.assemble import assemble_output
from email_thread_mcp.language import check_language, detect_language
from email_thread_mcp.server import detect_language as detect_language_tool
from email_thread_mcp.validate import validate_output

CONTRACTS = Path(__file__).parent.parent / "contracts"
DETECT_SCHEMA = json.loads((CONTRACTS / "detect_language.schema.json").read_text())
CHECK_SCHEMA = json.loads((CONTRACTS / "language.schema.json").read_text())

EMAILS = {
    "de": "Vielen Dank für Ihre Nachricht, wir prüfen die Rechnung und melden uns bis Freitag.",
    "fr": "Merci pour votre message, nous vérifions la facture et revenons vers vous vendredi.",
    "en": "Dear team, please find attached our remittance advice for the open invoices.",
    "ar": "شكرا لرسالتكم، سنقوم بمراجعة الفاتورة وإرسال نسخة منها قريبا.",
    "pl": "Dziękujemy za wiadomość, sprawdzimy fakturę i odpowiemy do piątku.",
}


@pytest.mark.parametrize("lang,text", list(EMAILS.items()))
def test_an_email_language_is_detected(lang, text) -> None:
    out = detect_language(text)
    assert (out["language"], out["status"]) == (lang, "detected")
    assert out["ranking"][0]["language"] == lang
    Draft202012Validator(DETECT_SCHEMA).validate(out)


@pytest.mark.parametrize("text", ["Thanks, noted", "Danke!", "", "INV-0001 / 24,770.50 EUR", "[Remittance PDF attached]"])
def test_a_short_reply_is_undecided(text) -> None:
    out = detect_language(text)
    assert out == {"schema_version": "1.0", "language": None, "status": "undecided", "ranking": [],
                   "resolved_language": None}
    Draft202012Validator(DETECT_SCHEMA).validate(out)


def test_a_mixed_text_is_undecided_with_its_ranking() -> None:
    out = detect_language("The Rechnung wird nächste Woche bezahlt, as agreed with the controller.")
    assert out["status"] == "undecided" and out["language"] is None
    assert 1 <= len(out["ranking"]) <= 3


def test_dialect_labels_count_towards_their_language() -> None:
    # The detector splits Arabic between ar, ary and arz; summed, the text is clearly Arabic.
    ranking = detect_language(EMAILS["ar"])["ranking"]
    assert [r["language"] for r in ranking].count("ar") == 1 and ranking[0]["probability"] >= 0.9


def test_a_detected_language_wins_over_the_fallback() -> None:
    out = detect_language(EMAILS["fr"], fallback="en")
    assert (out["language"], out["status"], out["resolved_language"]) == ("fr", "detected", "fr")
    Draft202012Validator(DETECT_SCHEMA).validate(out)


@pytest.mark.parametrize("fallback,resolved", [("de", "de"), (" EN ", "en"), ("nb", "nb")])
def test_an_undecided_text_resolves_to_the_fallback(fallback, resolved) -> None:
    out = detect_language("Thanks, noted", fallback=fallback)
    assert (out["language"], out["status"], out["resolved_language"]) == (None, "undecided", resolved)
    Draft202012Validator(DETECT_SCHEMA).validate(out)


def test_without_a_fallback_the_resolved_language_is_the_detection() -> None:
    assert detect_language("Thanks, noted")["resolved_language"] is None
    assert detect_language(EMAILS["pl"])["resolved_language"] == "pl"


@pytest.mark.parametrize("fallback", ["XX", "xx", "", "  ", "eng", "en-GB", "e", None])
def test_a_fallback_that_is_not_an_iso_639_1_code_resolves_to_null(fallback) -> None:
    assert detect_language("Thanks, noted", fallback=fallback)["resolved_language"] is None


def test_tool_passes_the_fallback_and_refuses_an_unrendered_one() -> None:
    assert detect_language_tool("Danke!", fallback="DE")["resolved_language"] == "de"
    with pytest.raises(ToolError, match="unrendered template"):
        detect_language_tool("Danke!", fallback="{{language_preference}}")


def test_tool_refuses_an_unrendered_text() -> None:
    with pytest.raises(ToolError, match="unrendered template"):
        detect_language_tool("{{latest_message.text}}")


def _envelope(language: str, draft_language: str) -> dict:
    return {
        "classification": {"categories": [{"category": "Copy of Invoice", "confidence": 0.9}], "reasoning": "x"},
        "draft_response": {"draft_subject": "AW: Rechnung", "draft_body": "Guten Tag,\n\nanbei die Kopie.",
                           "language": draft_language, "requires_human_review": True},
        "language": language,
    }


def _language_issues(result: dict) -> list[str]:
    return [i["path"] for i in result["issues"] if i["check"] == "language_matches_preference"]


def test_draft_in_the_email_language_and_the_rest_in_the_preference_passes() -> None:
    result = validate_output(_envelope("en", "de"), ["Copy of Invoice"], expected_language="en",
                             expected_draft_language="de")
    assert result["valid"] and _language_issues(result) == []


def test_a_draft_in_the_preference_instead_of_the_email_language_is_an_error() -> None:
    result = validate_output(_envelope("en", "en"), ["Copy of Invoice"], expected_language="en",
                             expected_draft_language="de")
    assert _language_issues(result) == ["$.draft_response.language"] and not result["valid"]


def test_without_a_draft_language_both_fields_follow_the_preference_as_in_0_8() -> None:
    result = validate_output(_envelope("en", "de"), ["Copy of Invoice"], expected_language="en")
    assert _language_issues(result) == ["$.draft_response.language"]


def test_a_draft_language_alone_checks_only_the_draft() -> None:
    out = assemble_output(_envelope("fr", "de"), expected_draft_language="DE")
    assert out["validation"]["valid"]


def test_assemble_output_reports_the_wrong_draft_language() -> None:
    out = assemble_output(_envelope("en", "en"), expected_language="en", expected_draft_language="de")
    assert [d["field"] for d in out["validation"]["error"]["details"]] == ["draft_response.language"]


# A word of another script in an otherwise correct text, as a drifting model writes it.
@pytest.mark.parametrize("text,expected,script,detected", [
    ("The customer raised a dispute بشأن a pricing discrepancy on the last invoice.", "en", "Arabic", None),
    ("Le client propose une approche orientée समाधान pour régler la facture en retard.", "fr", "Devanagari", None),
    ("The customer confirmed the 결제 will follow after the credit note is issued.", "en", "Hangul", "ko"),
    ("Der Kunde bestätigt die Zahlung через неделю nach Erhalt der Gutschrift.", "de", "Cyrillic", None),
])
def test_a_word_in_a_foreign_script_is_a_drift(text, expected, script, detected) -> None:
    out = check_language([text], expected)
    assert out["language_status"] == "drift"
    [issue] = out["language_issues"]
    assert (issue["foreign_script"], issue["detected"], issue["confidence"]) == (script, detected, 1.0)
    Draft202012Validator(CHECK_SCHEMA).validate(out)


def test_a_short_text_stays_unchecked_even_in_a_foreign_script() -> None:
    # Names and identifiers: a customer name in its own script is not a drift.
    out = check_language(["Send بشأن", "محمد العلي"], "en")
    assert out["language_status"] == "ok" and out["language_checked"] == 0


@pytest.mark.parametrize("text,expected", [
    ("Клиент сообщает, что счёт INV-0001 будет оплачен через Muster AG на следующей неделе.", "ru"),
    ("お客様によると、担当者が休暇中のため、請求書は来週支払われる予定です。", "ja"),
    ("고객은 다음 주에 청구서를 지불할 것이라고 말했습니다 계약서 확인 후.", "ko"),
    ("The customer says the 5 µm tolerance was met and the invoice will be paid.", "en"),
    ("The customer from Ｍｕｓｔｅｒ AG says the invoice will be paid next week.", "en"),
])
def test_own_script_latin_and_single_stray_letters_are_not_foreign(text, expected) -> None:
    out = check_language([text], expected)
    assert out["language_issues"] == [] and all(u["foreign_script"] is None for u in out["language_undecided"])


def test_a_detector_drift_also_names_a_foreign_script() -> None:
    out = check_language(["Le client propose une approche orientée समाधान pour régler la facture en retard."], "en")
    [issue] = out["language_issues"]
    assert (issue["detected"], issue["foreign_script"]) == ("fr", "Devanagari")

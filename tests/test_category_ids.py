import pytest
from mcp.server.mcpserver.exceptions import ToolError

from email_thread_mcp.assemble import assemble_output
from email_thread_mcp.server import assemble_output as assemble_tool
from email_thread_mcp.validate import validate_output

# Request shape since contract v2: an integer id and a description in any language, no name.
CATEGORIES = [
    {"id": 31, "description": "Customer requests invoice(s)"},
    {"id": 32, "description": "Kunde fragt nach einem Liefernachweis"},
    {"id": 37, "description": "Le client reporte le paiement sans engagement"},
    {"id": 42, "description": "E-mail informative non rilevanti per l'incasso"},
]


def _envelope(*cats, language: str = "en", draft_language: str | None = None) -> dict:
    return {
        "classification": {"categories": [{"category": c, "confidence": 0.9} for c in cats], "reasoning": "x"},
        "draft_response": {"draft_subject": "Re: x", "draft_body": "Dear Mr. Schneider,\n\nThank you.",
                           "language": draft_language or language, "requires_human_review": True},
        "language": language,
        "extracted_data": {"invoice_numbers": None},
    }


def _checks(result: dict) -> list[tuple[str, str, bool]]:
    return [(i["check"], i["severity"], i["corrected"]) for i in result["issues"]]


def test_ids_from_the_list_pass_untouched() -> None:
    result = validate_output(_envelope(37, 32), CATEGORIES)
    assert result["valid"] and result["issues"] == []


def test_a_digit_string_naming_an_allowed_id_is_corrected() -> None:
    result = validate_output(_envelope("37", " 32 "), CATEGORIES)
    assert result["valid"]
    assert [c["category"] for c in result["output"]["classification"]["categories"]] == [37, 32]
    assert _checks(result) == [("category_in_list", "warning", True)] * 2


@pytest.mark.parametrize("value", [99, "99", "Customer requests invoice(s)", True, 37.0, None])
def test_anything_else_is_an_error_and_is_kept(value) -> None:
    result = validate_output(_envelope(value), CATEGORIES)
    assert not result["valid"]
    assert _checks(result) == [("category_in_list", "error", False)]
    assert result["output"]["classification"]["categories"][0]["category"] == value


def test_a_duplicate_id_is_removed() -> None:
    result = validate_output(_envelope(37, "37"), CATEGORIES)
    assert result["valid"]
    assert [c["category"] for c in result["output"]["classification"]["categories"]] == [37]
    assert ("category_duplicate", "warning", True) in _checks(result)


def test_a_category_with_neither_name_nor_integer_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="integer id"):
        validate_output(_envelope(31), [{"id": "31", "description": "x"}])


def test_name_mode_is_unchanged_for_requests_with_names() -> None:
    named = [{"id": 1, "name": "Copy of Invoice", "description": "..."}]
    result = validate_output(_envelope("copy of invoice"), named)
    assert result["valid"]
    assert result["output"]["classification"]["categories"][0]["category"] == "Copy of Invoice"


def test_language_matching_the_preference_passes() -> None:
    out = assemble_output(_envelope(37, language="de"), allowed_categories=CATEGORIES, expected_language="DE")
    assert out["validation"]["valid"] and out["validation"]["issues"] == []


def test_a_draft_in_another_language_is_reported_not_relabelled() -> None:
    out = assemble_output(_envelope(37, language="de", draft_language="en"), allowed_categories=CATEGORIES,
                          expected_language="de")
    assert not out["validation"]["valid"]
    assert [(i["check"], i["path"]) for i in out["validation"]["issues"]] == [
        ("language_matches_preference", "$.draft_response.language")
    ]
    assert out["draft_response"]["language"] == "en"


def test_without_expected_language_the_check_is_skipped() -> None:
    out = assemble_output(_envelope(37, language="fr"), allowed_categories=CATEGORIES)
    assert out["validation"]["valid"]


def test_tool_accepts_categories_and_language_as_text() -> None:
    import json

    out = assemble_tool(json.dumps(_envelope(37, language="it")), allowed_categories=json.dumps(CATEGORIES),
                        expected_language="IT")
    assert out["validation"]["valid"]


def test_tool_reports_a_bad_category_list_as_a_tool_error() -> None:
    with pytest.raises(ToolError, match="integer id"):
        assemble_tool(_envelope(37), allowed_categories=[{"id": "x", "description": "y"}])


def _no_draft(actions: list[str]) -> dict:
    env = _envelope(42)
    env["draft_response"].update({"draft_subject": "", "draft_body": ""})
    env["suggested_actions"] = [{"action": a, "description": "x", "confidence": 0.9} for a in actions]
    return env


def test_no_draft_is_accepted_when_the_only_action_is_no_ar_action() -> None:
    out = assemble_output(_no_draft(["No AR Action"]), allowed_categories=CATEGORIES)
    assert out["validation"]["valid"], out["validation"]["issues"]


@pytest.mark.parametrize("actions", [[], ["Send Invoice Copy"], ["No AR Action", "Send Invoice Copy"]])
def test_no_draft_is_still_an_error_otherwise(actions) -> None:
    out = assemble_output(_no_draft(actions), allowed_categories=CATEGORIES)
    assert not out["validation"]["valid"]
    assert {i["check"] for i in out["validation"]["issues"]} >= {"draft_missing"}


def test_a_half_empty_draft_is_an_error_even_with_no_ar_action() -> None:
    env = _no_draft(["No AR Action"]); env["draft_response"]["draft_subject"] = "Re: x"
    out = assemble_output(env, allowed_categories=CATEGORIES)
    assert [i["path"] for i in out["validation"]["issues"] if i["check"] == "draft_missing"] == ["$.draft_response.draft_body"]

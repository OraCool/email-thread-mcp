import pytest
from mcp.server.mcpserver.exceptions import ToolError

from email_thread_mcp.server import _coerce
from email_thread_mcp.validate import normalise_name, validate_output

CATEGORIES = [
    {"id": 1, "name": "Copy of Invoice", "description": "..."},
    {"id": 2, "name": "Payment Advice / Confirmation", "description": "..."},
    {"id": 3, "name": "Payment Commitment", "description": "..."},
    {"id": 4, "name": "Dispute – Price", "description": "..."},
    {"id": 5, "name": "Other/ General", "description": "..."},
]


def _envelope(*cats: str, draft: str = "Dear Mr. Meyer,\n\nThank you.\n\nKind regards,\nAccounts Receivable Team", invoices=None) -> dict:
    return {
        "classification": {"categories": [{"category": c, "confidence": 0.9} for c in cats], "reasoning": "x"},
        "draft_response": {"draft_subject": "Re: x", "draft_body": draft, "language": "en", "requires_human_review": True},
        "extracted_data": {"invoice_numbers": invoices},
    }


def _checks(result: dict) -> list[tuple[str, str, bool]]:
    return [(i["check"], i["severity"], i["corrected"]) for i in result["issues"]]


def test_exact_names_pass_untouched() -> None:
    result = validate_output(_envelope("Payment Commitment", "Copy of Invoice"), CATEGORIES)
    assert result["valid"] and result["issues"] == []


def test_case_and_punctuation_differences_are_corrected() -> None:
    result = validate_output(_envelope("Payment commitment", "Dispute - Price", "other/general"), CATEGORIES)
    assert result["valid"]
    assert [c["category"] for c in result["output"]["classification"]["categories"]] == [
        "Payment Commitment", "Dispute – Price", "Other/ General"]
    assert all(c == ("category_in_list", "warning", True) for c in _checks(result))


def test_a_prefix_is_never_mapped() -> None:
    result = validate_output(_envelope("Invoice", "Payment"), CATEGORIES)
    assert not result["valid"]
    assert _checks(result) == [("category_in_list", "error", False)] * 2
    assert [c["category"] for c in result["output"]["classification"]["categories"]] == ["Invoice", "Payment"]


def test_duplicates_after_correction_are_removed() -> None:
    result = validate_output(_envelope("Payment Commitment", "payment commitment"), CATEGORIES)
    assert result["valid"]
    assert len(result["output"]["classification"]["categories"]) == 1
    assert ("category_duplicate", "warning", True) in _checks(result)


def test_invoice_numbers_are_cleaned() -> None:
    result = validate_output(_envelope("Payment Commitment", invoices=["(9000020", "9000020", "06.10.2026", "9000021"]), CATEGORIES)
    assert result["output"]["extracted_data"]["invoice_numbers"] == ["9000020", "9000021"]


def test_human_review_flag_is_forced() -> None:
    env = _envelope("Payment Commitment")
    env["draft_response"]["requires_human_review"] = False
    result = validate_output(env, CATEGORIES)
    assert result["output"]["draft_response"]["requires_human_review"] is True


def test_greeting_the_ar_team_member_is_reported() -> None:
    thread = {"latest_message": {"greeting_name": "Ms. Lange"}, "reply_to_name": "Clara Bauer"}
    result = validate_output(_envelope("Payment Commitment", draft="Dear Ms. Lange,\n\nThanks."), CATEGORIES, thread)
    assert ("greeting_matches_reply_to", "warning", False) in _checks(result)
    ok = validate_output(_envelope("Payment Commitment", draft="Dear Ms. Bauer,\n\nThanks."), CATEGORIES, thread)
    assert ok["issues"] == []


def test_promised_document_without_placeholder_is_reported() -> None:
    result = validate_output(_envelope("Copy of Invoice", draft="Dear Mr. Svensson,\n\nWe will resend a copy of invoice 9000022."), CATEGORIES)
    assert ("attachment_placeholder", "warning", False) in _checks(result)
    ok = validate_output(_envelope("Copy of Invoice", draft="Dear Mr. Svensson,\n\nWe will send you a copy of invoice 9000022. [Invoice PDF attached]"), CATEGORIES)
    assert ok["issues"] == []


def test_normalise_name_keeps_words() -> None:
    assert normalise_name("Dispute – Price") == normalise_name("dispute-price")
    assert normalise_name("Invoice") != normalise_name("Copy of Invoice")


def test_coerce_accepts_json_and_python_repr() -> None:
    assert _coerce('[{"name": "A"}]') == [{"name": "A"}]
    assert _coerce("[{'name': 'A'}]") == [{"name": "A"}]
    assert _coerce({"a": 1}) == {"a": 1}


def test_coerce_rejects_text_with_a_tool_error_naming_the_argument() -> None:
    with pytest.raises(ToolError, match="output is neither JSON nor a Python literal"):
        _coerce("{not json", "output")


def test_an_empty_draft_is_an_error() -> None:
    env = _envelope("Payment Commitment")
    env["draft_response"].update(draft_subject=None, draft_body=None)
    thread = {"latest_message": {}, "reply_to_name": "Clara Bauer"}
    result = validate_output(env, CATEGORIES, thread)
    assert not result["valid"]
    assert [(i["check"], i["path"]) for i in result["issues"]] == [
        ("draft_missing", "$.draft_response.draft_subject"),
        ("draft_missing", "$.draft_response.draft_body"),
    ]


def test_a_blank_body_is_an_error() -> None:
    result = validate_output(_envelope("Payment Commitment", draft="  \n"), CATEGORIES)
    assert ("draft_missing", "error", False) in _checks(result)


def test_markup_in_the_subject_is_removed() -> None:
    env = _envelope("Payment Commitment")
    env["draft_response"]["draft_subject"] = "Re: <email_subject>Re: Delivery question</email_subject>\n"
    result = validate_output(env, CATEGORIES)
    assert result["valid"]
    assert result["output"]["draft_response"]["draft_subject"] == "Re: Delivery question"
    assert _checks(result) == [("draft_subject_markup", "warning", True)]


@pytest.mark.parametrize(("draft", "expected"), [
    ("Dear Mr. Exclusions,\n\nThank you.", "Dear Mr. Berg,\n\nThank you."),
    ("Dear Ms. Grüßen,\n\nThank you.", "Dear Mr. Berg,\n\nThank you."),
    ("Sehr geehrter Herr Hinweis,\n\nvielen Dank.", "Sehr geehrter Mr. Berg,\n\nvielen Dank."),
])
def test_a_greeting_naming_someone_else_is_replaced(draft: str, expected: str) -> None:
    thread = {"latest_message": {"greeting_name": "Ms. Lange"}, "reply_to_name": "Jan Berg",
              "reply_to_greeting": "Mr. Berg"}
    result = validate_output(_envelope("Payment Commitment", draft=draft), CATEGORIES, thread)
    assert result["output"]["draft_response"]["draft_body"] == expected
    assert _checks(result) == [("greeting_matches_reply_to", "warning", True)]
    assert result["valid"]


@pytest.mark.parametrize("draft", ["Dear Eva,\n\nThank you.", "Dear Ms. Novak,\n\nThank you.", "Dear Eva Novak,\n\nThanks."])
def test_a_greeting_with_part_of_the_name_is_kept(draft: str) -> None:
    thread = {"latest_message": {}, "reply_to_name": "Eva Novak", "reply_to_greeting": "Eva Novak"}
    result = validate_output(_envelope("Payment Commitment", draft=draft), CATEGORIES, thread)
    assert result["output"]["draft_response"]["draft_body"] == draft
    assert result["issues"] == []


def test_no_known_address_means_no_replacement() -> None:
    thread = {"latest_message": {}, "reply_to_name": None, "reply_to_greeting": None}
    result = validate_output(_envelope("Payment Commitment", draft="Dear Sir or Madam,\n\nThanks."), CATEGORIES, thread)
    assert result["output"]["draft_response"]["draft_body"] == "Dear Sir or Madam,\n\nThanks."
    assert result["issues"] == []

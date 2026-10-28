"""A workflow whose variables are not set passes `{{...}}` placeholders through unrendered (seen on
CodeMIE when the request was not valid JSON). The tools refuse them with a clear error, so the
workflow stops at its first step instead of running every model on empty input."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from email_thread_mcp.server import check_language, parse_email_thread


@pytest.mark.parametrize("body", ["{{email.parsed_body_structured.plain_text}}", "  {{ email.body }}\n", "", "   \r\n"])
def test_parse_refuses_unrendered_or_empty_body(body) -> None:
    with pytest.raises(ToolError, match="unrendered template|body is empty"):
        parse_email_thread(body)


def test_parse_still_accepts_an_email_that_mentions_braces() -> None:
    body = "Hi Anna,\n\nour portal shows {{invoice}} instead of the number. Please check INV-1.\n\nBest regards,\nThomas"
    assert "portal" in parse_email_thread(body)["latest_message"]["text"]


@pytest.mark.parametrize("expected", ["{{email.language_preference | lower}}", "{{email.language", "", "English please"])
def test_check_language_refuses_an_unrendered_or_invalid_language(expected) -> None:
    with pytest.raises(ToolError, match="unrendered template|language code"):
        check_language(["Send the signed proof of delivery to the customer today."], expected)


def test_check_language_refuses_unrendered_texts() -> None:
    with pytest.raises(ToolError, match="unrendered template"):
        check_language("{{ suggested_actions | tojson }}", "en")


@pytest.mark.parametrize("expected", ["en", "EN", "pt-BR", "zh_TW", "nb"])
def test_check_language_accepts_language_codes(expected) -> None:
    assert check_language(["Send the signed proof of delivery to the customer today."], expected)["schema_version"] == "1.0"

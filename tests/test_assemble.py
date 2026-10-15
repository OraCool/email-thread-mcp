import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from email_thread_mcp.assemble import assemble_output
from email_thread_mcp.server import assemble_output as assemble_tool

CATEGORIES = [{"id": 1, "name": "Send Statement"}, {"id": 2, "name": "Payment Plan"}]

SCHEMA = {
    "type": "object",
    "required": ["email_id", "classification", "draft_response", "priority"],
    "properties": {
        "email_id": {"type": "string"},
        "classification": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object", "properties": {"category": {"enum": ["Send Statement", "Payment Plan"]}}},
                }
            },
        },
        "priority": {"enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        "extracted_data": {"type": "object", "properties": {"due_date": {"type": ["string", "null"], "format": "date"}}},
    },
}


def _envelope(**over) -> dict:
    env = {
        "email_id": "MSG-1",
        "classification": {"categories": [{"category": "Send Statement", "confidence": 0.9}], "reasoning": "x"},
        "draft_response": {"draft_subject": "Re: x", "draft_body": "Dear Mr. Berg,\n\nThanks.", "language": "en",
                           "requires_human_review": True},
        "priority": "MEDIUM",
        "extracted_data": {"invoice_numbers": ["9000020"], "due_date": "2026-10-01"},
    }
    env.update(over)
    return env


def _checks(out: dict) -> list[tuple[str, str]]:
    return [(i["check"], i["path"]) for i in out["validation"]["issues"]]


def test_valid_envelope_passes_unchanged() -> None:
    env = _envelope()
    out = assemble_output(env, SCHEMA, CATEGORIES)
    assert out["validation"] == {"schema_version": "1.0", "valid": True, "issues": []}
    assert {k: v for k, v in out.items() if k != "validation"} == env


def test_schema_violations_are_reported_with_paths() -> None:
    env = _envelope(priority="URGENT", extracted_data={"due_date": "01.10.2026"})
    del env["email_id"]
    out = assemble_output(env, SCHEMA)
    assert not out["validation"]["valid"]
    assert _checks(out) == [("schema", "$"), ("schema", "$.extracted_data.due_date"), ("schema", "$.priority")]


def test_corrections_run_before_the_schema() -> None:
    env = _envelope(classification={"categories": [{"category": "send statement", "confidence": 0.9}]})
    out = assemble_output(env, SCHEMA, CATEGORIES)
    assert out["validation"]["valid"]
    assert out["classification"]["categories"][0]["category"] == "Send Statement"
    assert _checks(out) == [("category_in_list", "$.classification.categories[0].category")]


def test_without_categories_the_other_checks_still_run() -> None:
    env = _envelope(extracted_data={"invoice_numbers": ["(9000020", "9000020"]})
    env["draft_response"]["requires_human_review"] = False
    out = assemble_output(env)
    assert out["extracted_data"]["invoice_numbers"] == ["9000020"]
    assert out["draft_response"]["requires_human_review"] is True
    assert out["validation"]["valid"]


def test_invalid_schema_is_an_error() -> None:
    with pytest.raises(ValueError, match="not a valid JSON Schema"):
        assemble_output(_envelope(), {"type": "no-such-type"})


def test_tool_accepts_rendered_text() -> None:
    out = assemble_tool(json.dumps(_envelope()), json.dumps(SCHEMA), json.dumps(CATEGORIES))
    assert out["validation"]["valid"]


def test_tool_names_an_unrendered_template() -> None:
    with pytest.raises(ToolError, match="envelope is an unrendered template"):
        assemble_tool('{"email_id": {{email.id|tojson}}}')


def test_tool_keeps_braces_inside_valid_json() -> None:
    env = _envelope()
    env["draft_response"]["draft_body"] = "Dear Mr. Berg, see {{ref}}."
    assert assemble_tool(json.dumps(env))["draft_response"]["draft_body"].endswith("{{ref}}.")


ACTIONS = ["Mail Billing Pdf", "Log Payment Plan", "Match Remittance"]


def _actions(*names: str) -> list[dict]:
    return [{"action": n, "description": "x", "confidence": 0.8} for n in names]


def test_action_case_and_underscore_variants_are_corrected() -> None:
    out = assemble_output(_envelope(suggested_actions=_actions("Log payment plan", "mail_billing_pdf")),
                          allowed_actions=ACTIONS)
    assert [a["action"] for a in out["suggested_actions"]] == ["Log Payment Plan", "Mail Billing Pdf"]
    assert [(i["check"], i["corrected"]) for i in out["validation"]["issues"]] == [("action_in_list", True)] * 2
    assert out["validation"]["valid"]


def test_a_new_action_stays_with_a_warning() -> None:
    out = assemble_output(_envelope(suggested_actions=_actions("Open Legal Case")), allowed_actions=ACTIONS)
    assert [a["action"] for a in out["suggested_actions"]] == ["Open Legal Case"]
    assert [(i["check"], i["severity"], i["corrected"]) for i in out["validation"]["issues"]] == [
        ("action_in_list", "warning", False)]
    assert out["validation"]["valid"]


def test_an_action_duplicated_by_correction_is_removed() -> None:
    out = assemble_output(_envelope(suggested_actions=_actions("Match Remittance", "match remittance")), allowed_actions=ACTIONS)
    assert [a["action"] for a in out["suggested_actions"]] == ["Match Remittance"]
    assert ("action_duplicate", "$.suggested_actions[1].action") in _checks(out)


def test_tool_accepts_actions_as_text() -> None:
    env = _envelope(suggested_actions=_actions("log payment plan"))
    out = assemble_tool(json.dumps(env), allowed_actions=json.dumps(ACTIONS))
    assert out["suggested_actions"][0]["action"] == "Log Payment Plan"

"""validate_request: parse and check the incoming request, and return a ready error envelope that names
the request's own fields. Synthetic requests only."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError

from email_thread_mcp.server import validate_request

SCHEMA = {
    "type": "object",
    "required": ["email"],
    "properties": {
        "email": {
            "type": "object",
            "required": ["id", "subject", "parsed_body_structured", "language_preference", "categories"],
            "properties": {
                "id": {"type": "string", "pattern": "\\S"},
                "subject": {"type": "string"},
                "parsed_body_structured": {
                    "type": "object",
                    "required": ["plain_text"],
                    "properties": {"plain_text": {"type": "string", "pattern": "\\S"}},
                },
                "language_preference": {"type": "string", "pattern": "^[A-Za-z]{2}$"},
                "categories": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "description"],
                        "properties": {"id": {"type": "integer"}, "description": {"type": "string", "pattern": "\\S"}},
                    },
                },
            },
        }
    },
}


def _request(**email) -> dict:
    base = {
        "id": "MSG-1",
        "subject": "Invoice INV-90001",
        "parsed_body_structured": {"plain_text": "Hello, we will pay invoice INV-90001 next Friday. Regards, Test"},
        "language_preference": "EN",
        "categories": [{"id": 1, "description": "Customer promises to pay"}, {"id": 2, "description": "Informational e-mails"}],
    }
    base.update(email)
    return {"email": {k: v for k, v in base.items() if v is not None}}


def _fields(result: dict) -> dict[str, str]:
    return {d["field"]: d["problem"] for d in result["error"]["details"]}


def test_a_valid_request_passes_and_echoes_its_id() -> None:
    result = validate_request(json.dumps(_request()), json.dumps(SCHEMA))
    assert result == {"schema_version": "1.0", "valid": True, "error": None, "email_id": "MSG-1"}


def test_a_parsed_request_is_accepted_too() -> None:
    assert validate_request(_request(), SCHEMA)["valid"] is True


def test_missing_fields_are_named_as_the_caller_writes_them() -> None:
    req = _request(language_preference=None, parsed_body_structured=None)
    req["email"]["body"] = "the body under the old name"
    result = validate_request(json.dumps(req), SCHEMA)
    assert result["valid"] is False
    assert result["error"]["error"] is True and result["error"]["error_code"] == "INVALID_REQUEST"
    assert result["error"]["email_id"] == "MSG-1"
    fields = _fields(result)
    assert fields["email.language_preference"] == "missing"
    assert fields["email.parsed_body_structured"] == "missing"
    assert "email.language_preference: missing" in result["error"]["message"]


@pytest.mark.parametrize(
    ("category", "field", "problem"),
    [
        ({"id": 1, "name": "Promise to pay", "description": "x"}, "email.categories[0].name", "not allowed"),
        ({"id": 1}, "email.categories[0].description", "missing"),
        ({"id": "1", "description": "x"}, "email.categories[0].id", "must be an integer"),
        ({"id": 1, "description": "   "}, "email.categories[0].description", "empty"),
    ],
)
def test_contract_v1_or_broken_categories_are_reported(category, field, problem) -> None:
    result = validate_request(_request(categories=[category]), SCHEMA)
    assert _fields(result)[field] == problem


def test_empty_values_say_empty() -> None:
    result = validate_request(_request(categories=[], parsed_body_structured={"plain_text": " \n"}), SCHEMA)
    fields = _fields(result)
    assert fields["email.categories"] == "empty"
    assert fields["email.parsed_body_structured.plain_text"] == "empty"


def test_a_language_that_is_not_a_code_shows_the_value() -> None:
    problem = _fields(validate_request(_request(language_preference="English"), SCHEMA))["email.language_preference"]
    assert problem.startswith("must match") and '"English"' in problem


def test_non_breaking_spaces_from_a_rich_text_copy_are_explained() -> None:
    text = json.dumps(_request(), indent=2).replace("  ", "  ")
    result = validate_request(text, SCHEMA)
    problem = _fields(result)["request"]
    assert problem.startswith("not valid JSON") and "U+00A0" in problem and "normal spaces" in problem
    assert result["email_id"] is None


@pytest.mark.parametrize("text", ["{{user_input}}", "", "  \n"])
def test_an_unrendered_or_empty_request_is_invalid_not_an_exception(text) -> None:
    result = validate_request(text, SCHEMA)
    assert result["valid"] is False and result["error"]["details"][0]["field"] == "request"


def test_without_a_schema_only_the_syntax_is_checked() -> None:
    assert validate_request('{"email": {}}')["valid"] is True
    assert validate_request("{not json")["valid"] is False


def test_an_invalid_schema_is_a_configuration_error() -> None:
    with pytest.raises(ToolError, match="not a valid JSON Schema"):
        validate_request(_request(), {"type": "no-such-type"})
    with pytest.raises(ToolError, match="unrendered template"):
        validate_request(_request(), "{{request_schema}}")


def test_many_problems_are_capped_and_summarised() -> None:
    cats = [{"id": str(i)} for i in range(30)]
    result = validate_request(_request(categories=cats), SCHEMA)
    assert len(result["error"]["details"]) == 20
    assert "more)" in result["error"]["message"]


def test_the_result_matches_its_contract() -> None:
    contract = json.loads((Path(__file__).parents[1] / "contracts" / "request.schema.json").read_text())
    for req in (_request(), _request(language_preference=None), "{bad"):
        Draft202012Validator(contract).validate(validate_request(req, SCHEMA))


@pytest.mark.parametrize("ref", ["http://127.0.0.1:9/schema.json", "file:///etc/passwd"])
def test_a_schema_ref_is_never_fetched(ref) -> None:
    with pytest.raises(ToolError, match="does not resolve inside it"):
        validate_request(_request(), {"$ref": ref})


def test_a_local_ref_still_resolves() -> None:
    schema = {"$defs": {"email": SCHEMA["properties"]["email"]}, "type": "object",
              "required": ["email"], "properties": {"email": {"$ref": "#/$defs/email"}}}
    assert validate_request(_request(), schema)["valid"] is True
    assert validate_request(_request(language_preference=None), schema)["valid"] is False

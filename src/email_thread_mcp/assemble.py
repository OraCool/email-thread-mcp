"""Final step of a pipeline: check an envelope that was assembled without an LLM.

A workflow engine renders the envelope from the earlier steps' values (a template with a JSON filter),
so identifiers, invoice numbers and the draft arrive exactly as those steps produced them. This module
runs the validate_output checks on the result, then checks it against a JSON Schema.

Output shape: the envelope, corrected as validate_output corrects it, plus a `validation` object
(contracts/assemble.schema.json, schema_version 1.0).
"""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from .validate import (
    _allowed_categories,
    _category_names,
    _check_actions,
    _check_categories,
    _check_draft,
    _check_invoices,
    _check_language,
)

SCHEMA_VERSION = "1.0"

_MAX_SCHEMA_ERRORS = 50


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def schema_issues(envelope: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    """One error issue per schema violation, ordered by path."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise ValueError(f"schema is not a valid JSON Schema: {e.message}") from None
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(envelope), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    return [
        {"check": "schema", "severity": "error", "path": _json_path(e.absolute_path), "message": e.message, "corrected": False}
        for e in errors[:_MAX_SCHEMA_ERRORS]
    ]


def assemble_output(
    envelope: dict[str, Any],
    schema: dict[str, Any] | None = None,
    allowed_categories: list[Any] | None = None,
    thread: dict[str, Any] | None = None,
    allowed_actions: list[Any] | None = None,
    expected_language: str | None = None,
) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be a JSON object")
    out = copy.deepcopy(envelope)
    issues: list[dict[str, Any]] = []
    if allowed_categories:
        _check_categories(out, _allowed_categories(allowed_categories), issues)
    if allowed_actions:
        _check_actions(out, _category_names(allowed_actions), issues)
    if expected_language:
        _check_language(out, expected_language, issues)
    _check_invoices(out, issues)
    _check_draft(out, thread, issues)
    # After the corrections, so a category whose case was fixed does not fail the schema's enum.
    if schema:
        issues += schema_issues(out, schema)
    out["validation"] = {
        "schema_version": SCHEMA_VERSION,
        "valid": not any(i["severity"] == "error" and not i["corrected"] for i in issues),
        "issues": issues,
    }
    return out

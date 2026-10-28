"""First step of a pipeline: check the incoming request before any model sees it.

A workflow engine fills its variables from the request only when the request parses as JSON; a
request copied from a rich-text editor (non-breaking spaces as indentation) or one with a missing field
otherwise runs every step on empty input. This module parses the raw request, checks it against the
caller's JSON Schema and, when it fails, returns a ready error envelope that names the request's own
fields, so the caller learns what to fix.

Output shape: contracts/request.schema.json, schema_version 1.0.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable

SCHEMA_VERSION = "1.0"
ERROR_CODE = "INVALID_REQUEST"

_MAX_DETAILS = 20
_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")
# Whitespace that JSON does not allow between tokens, typically pasted from a rich-text editor.
_FOREIGN_SPACE = re.compile("[\u00a0\u2000-\u200b\u202f\u205f\u3000\ufeff]")


def _field(parts: Any) -> str:
    """A JSON Schema error path as the caller writes it: email.categories[0].description."""
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "request"


def _join(base: str, name: str) -> str:
    return name if base == "request" else f"{base}.{name}"


def _problems(error: Any) -> list[tuple[str, str]]:
    """One schema error as (field, problem) pairs, in words a caller can act on."""
    field = _field(error.absolute_path)
    kind, value = error.validator, error.validator_value
    if kind == "required":
        missing = [name for name in value if isinstance(error.instance, dict) and name not in error.instance]
        return [(_join(field, name), "missing") for name in missing] or [(field, error.message)]
    if kind == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set((error.schema.get("properties") or {}).keys())
        extra = [name for name in error.instance if name not in allowed]
        return [(_join(field, name), "not allowed") for name in extra] or [(field, error.message)]
    if kind == "type":
        expected = " or ".join(value) if isinstance(value, list) else value
        return [(field, f"must be {'an' if str(expected)[:1] in 'aeiou' else 'a'} {expected}")]
    if kind == "minLength" and value == 1 or kind == "minItems" and value == 1:
        return [(field, "empty")]
    if kind == "pattern" and isinstance(error.instance, str) and not error.instance.strip():
        return [(field, "empty")]
    if kind in ("pattern", "enum", "const", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
                "uniqueItems", "format"):
        shown = json.dumps(error.instance, ensure_ascii=False)
        shown = shown if len(shown) <= 60 else shown[:57] + "..."
        rule = {"pattern": f"must match {value}", "enum": f"must be one of {json.dumps(value)}",
                "const": f"must be {json.dumps(value)}", "uniqueItems": "has duplicate items",
                "format": f"must be a valid {value}"}.get(kind, f"violates {kind} {value}")
        return [(field, f"{rule}, got {shown}")]
    return [(field, error.message[:200])]


def _envelope(message: str, details: list[dict[str, str]], email_id: str | None) -> dict[str, Any]:
    return {"error": True, "error_code": ERROR_CODE, "message": message, "details": details, "email_id": email_id}


def _result(error: dict[str, Any] | None, email_id: str | None) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "valid": error is None, "error": error, "email_id": email_id}


def _pointer(doc: Any, pointer: str) -> Any:
    for token in [t for t in pointer.split("/") if t]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, dict):
            doc = doc.get(token)
        elif isinstance(doc, list) and token.isdigit() and int(token) < len(doc):
            doc = doc[int(token)]
        else:
            return None
    return doc


def _parse(request: Any) -> tuple[Any, dict[str, str] | None]:
    """The request as a JSON value, or the one detail that says why it is not one."""
    if isinstance(request, (dict, list)):
        return request, None
    if not isinstance(request, str) or not request.strip():
        return None, {"field": "request", "problem": "empty"}
    text = request.strip()
    if _PLACEHOLDER.fullmatch(text):
        return None, {"field": "request",
                      "problem": f"not rendered ({text[:80]}): the workflow could not read the request as JSON"}
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        problem = f"not valid JSON: {e.msg} at line {e.lineno} column {e.colno}"
        bad = _FOREIGN_SPACE.search(text)
        if bad:
            problem += (f"; it contains U+{ord(bad.group()):04X} spaces (often pasted from a rich-text editor), "
                        "which JSON does not allow outside strings: replace them with normal spaces")
        return None, {"field": "request", "problem": problem}
    except (RecursionError, MemoryError):
        return None, {"field": "request", "problem": "nested too deeply"}


def validate_request(request: Any, schema: dict[str, Any] | None = None, id_pointer: str = "/email/id") -> dict[str, Any]:
    """Parse and check a request; see the module docstring.

    Args:
        request: the raw request text (or an already parsed JSON value).
        schema: a JSON Schema (draft 2020-12) the request must satisfy. Optional; without it only the
            JSON syntax is checked.
        id_pointer: JSON Pointer to the request's identifier, echoed as `email_id` in the result and
            in the error envelope.

    Raises:
        ValueError: the schema itself is not a valid JSON Schema (a configuration error, not a bad request).
    """
    if schema is not None:
        if not isinstance(schema, dict):
            raise ValueError("schema must be a JSON object")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as e:
            raise ValueError(f"schema is not a valid JSON Schema: {e.message}") from None

    doc, problem = _parse(request)
    if problem:
        return _result(_envelope(f"The request is {problem['problem']}.", [problem], None), None)

    found = _pointer(doc, id_pointer)
    email_id = found if isinstance(found, str) and found.strip() else None
    if schema is None:
        return _result(None, email_id)

    details: list[dict[str, str]] = []
    # An empty registry: only refs inside the schema resolve, never a URL or a file (the schema is an argument).
    validator = Draft202012Validator(schema, registry=Registry(), format_checker=FormatChecker())
    try:
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    except Unresolvable as e:
        raise ValueError(f"schema has a $ref that does not resolve inside it: {e}") from None
    for error in errors:
        for field, text in _problems(error):
            if {"field": field, "problem": text} not in details:
                details.append({"field": field, "problem": text})
    if not details:
        return _result(None, email_id)
    shown = "; ".join(f"{d['field']}: {d['problem']}" for d in details[:3])
    more = f" (and {len(details) - 3} more)" if len(details) > 3 else ""
    word = "problem" if len(details) == 1 else "problems"
    return _result(_envelope(f"The request has {len(details)} {word}: {shown}{more}.", details[:_MAX_DETAILS], email_id),
                   email_id)

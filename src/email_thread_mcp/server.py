"""MCP server (stdio) exposing the AR email tools. MCP clients start it with `uvx email-thread-mcp`.

Some clients render structured arguments through a template engine first, so they may arrive as
text: JSON or a Python-style repr. Both are accepted.
"""

from __future__ import annotations

import ast
import json
from importlib.metadata import version
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .assemble import assemble_output as _assemble_output
from .language import check_language as _check_language
from .thread import parse_email_thread as _parse_email_thread
from .validate import validate_output as _validate_output

mcp = MCPServer("email-thread-mcp", version=version("email-thread-mcp"))


def _coerce(value: Any, name: str = "argument") -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    except (RecursionError, MemoryError):
        raise ToolError(f"{name} is nested too deeply") from None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError):
        # ToolError, not ValueError: the SDK hides a plain exception's text from the caller.
        raise ToolError(f"{name} is neither JSON nor a Python literal") from None
    except (RecursionError, MemoryError):
        raise ToolError(f"{name} is nested too deeply") from None


@mcp.tool()
def parse_email_thread(body: str, ar_mailboxes: list[str] | str | None = None) -> dict[str, Any]:
    """Split an inbound email body (HTML or plain text, newest message on top) into the latest
    message, its signer and greeting, and the quoted history (oldest first), per the thread contract.
    Also returns who to greet and how (reply_to_name, reply_to_greeting) and our_signature, the
    signature block of our quoted message to sign the reply with.

    Args:
        body: the raw email body (HTML or plain text), unchanged.
        ar_mailboxes: addresses (or '@domain' entries) of the AR team; quoted messages from these are
            role 'recipient' (us), messages sent to them are role 'sender' (the customer).
    """
    mailboxes = _coerce(ar_mailboxes, "ar_mailboxes") if isinstance(ar_mailboxes, str) else ar_mailboxes
    if isinstance(mailboxes, str):
        mailboxes = [mailboxes]
    return _parse_email_thread(body, mailboxes or [])


@mcp.tool()
def validate_output(
    output: dict[str, Any] | str,
    categories: list[Any] | str,
    thread: dict[str, Any] | str | None = None,
    expected_language: str | None = None,
) -> dict[str, Any]:
    """Check the agent's output envelope: categories must come from the request's list,
    invoice numbers clean, the draft greets the right person and marks promised attachments.
    Safe corrections are applied to the returned `output`; everything else is reported in `issues`.

    Args:
        output: the final envelope (classification, draft_response, extracted_data, ...).
        categories: the allowed categories: objects with `name` (or plain names), matched by name; or
            objects with an integer `id` and no `name`, matched by id.
        thread: the parse_email_thread result, used for the greeting check. Optional.
        expected_language: the requested response language (ISO 639-1). Optional; when given,
            `language` and `draft_response.language` must name it.
    """
    try:
        return _validate_output(
            _coerce(output, "output"), _coerce(categories, "categories") or [], _coerce(thread, "thread"),
            expected_language or None,
        )
    except ValueError as e:
        raise ToolError(str(e)) from None


@mcp.tool()
def assemble_output(
    envelope: dict[str, Any] | str,
    schema: dict[str, Any] | str | None = None,
    allowed_categories: list[Any] | str | None = None,
    thread: dict[str, Any] | str | None = None,
    allowed_actions: list[Any] | str | None = None,
    expected_language: str | None = None,
) -> dict[str, Any]:
    """Final step: check an envelope that the workflow assembled from the earlier steps' values,
    without an LLM copying them. Runs the validate_output checks, then validates against `schema`.
    Returns the envelope (with validate_output's safe corrections) plus `validation`:
    {schema_version, valid, issues}. A failed check is reported there; it does not raise.

    Args:
        envelope: the final envelope (classification, draft_response, extracted_data, ...).
        schema: a JSON Schema (draft 2020-12) the envelope must satisfy. Optional.
        allowed_categories: the request's categories. Optional; without it the category check is
            skipped. Objects with `name` (or plain names) are matched by name; objects with an integer
            `id` and no `name` by id, where only a digit string naming an allowed id is corrected.
        thread: the parse_email_thread result, used for the greeting check. Optional.
        allowed_actions: the suggested-action vocabulary (objects with `name`, or plain names). Optional.
            A case or punctuation variant is corrected; an action outside it stays, with a warning.
        expected_language: the requested response language (ISO 639-1). Optional; when given,
            `language` and `draft_response.language` must name it. A mismatch is an error, not corrected.
    """
    args = {}
    for name, value in (("envelope", envelope), ("schema", schema),
                        ("allowed_categories", allowed_categories), ("thread", thread),
                        ("allowed_actions", allowed_actions)):
        try:
            args[name] = _coerce(value, name)
        except ToolError:
            if isinstance(value, str) and "{{" in value:
                raise ToolError(f"{name} is an unrendered template: a variable it uses is not set") from None
            raise
    args["expected_language"] = expected_language or None
    try:
        return _assemble_output(**args)
    except ValueError as e:
        raise ToolError(str(e)) from None


@mcp.tool()
def check_language(texts: list[Any] | str, expected: str) -> dict[str, Any]:
    """Report every text clearly written in another language than `expected` (ISO 639-1), so a
    pipeline can retry the step that wrote it. Returns {schema_version, language_ok, expected,
    checked, language_issues: [{index, expected, detected, text}]}. Short texts, names, identifiers
    and an unsupported `expected` are never reported.

    Args:
        texts: the free texts to check (a list of strings, or one string).
        expected: the requested language, an ISO 639-1 code such as "en" (case-insensitive).
    """
    return _check_language(_coerce(texts, "texts") or [], expected or "")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

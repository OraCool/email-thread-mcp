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
) -> dict[str, Any]:
    """Check the agent's output envelope: category names must come from the request's list,
    invoice numbers clean, the draft greets the right person and marks promised attachments.
    Safe corrections are applied to the returned `output`; everything else is reported in `issues`.

    Args:
        output: the final envelope (classification, draft_response, extracted_data, ...).
        categories: the allowed categories (objects with `name`, or plain names).
        thread: the parse_email_thread result, used for the greeting check. Optional.
    """
    return _validate_output(
        _coerce(output, "output"), _coerce(categories, "categories") or [], _coerce(thread, "thread")
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

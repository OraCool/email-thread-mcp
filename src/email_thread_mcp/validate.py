"""Deterministic checks of the agent's output envelope.

Output shape: contracts/validation.schema.json (schema_version 1.0).

Corrects only what is unambiguous and reports the rest. In particular it never maps a category by
similarity or prefix: "Invoice" is not silently turned into "Copy of Invoice".
"""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

SCHEMA_VERSION = "1.0"

_DATE_LIKE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")
_PROMISES_DOCUMENT = re.compile(
    r"\b(send|attach|resend|forward|provide)\b[^.\n]{0,60}\b(copy|copies|invoice|statement|proof of delivery|pod|credit note)\b",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\[[^\]]*attached\]", re.IGNORECASE)
_HONORIFIC = re.compile(r"^(mr|ms|mrs|dr|frau|herr)\.?\s+", re.IGNORECASE)


def normalise_name(name: str) -> str:
    """Case, Unicode form, quotes, dashes, slashes and whitespace - nothing that changes the words."""
    s = unicodedata.normalize("NFKC", name).casefold()
    s = re.sub(r"[‘’“”\"']", "", s)
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"\s*([/-])\s*", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _issue(check: str, severity: str, path: str, message: str, corrected: bool) -> dict[str, Any]:
    return {"check": check, "severity": severity, "path": path, "message": message, "corrected": corrected}


def _category_names(categories: list[Any]) -> list[str]:
    return [c["name"] if isinstance(c, dict) else str(c) for c in categories]


def _check_categories(out: dict[str, Any], allowed: list[str], issues: list[dict[str, Any]]) -> None:
    cats = (out.get("classification") or {}).get("categories")
    if not isinstance(cats, list):
        issues.append(_issue("category_in_list", "error", "$.classification.categories", "missing categories list", False))
        return
    by_norm: dict[str, list[str]] = {}
    for name in allowed:
        by_norm.setdefault(normalise_name(name), []).append(name)

    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for i, item in enumerate(cats):
        path = f"$.classification.categories[{i}].category"
        value = item.get("category") if isinstance(item, dict) else None
        if not isinstance(value, str):
            issues.append(_issue("category_in_list", "error", path, "category is not a string", False))
            kept.append(item)
            continue
        if value not in allowed:
            matches = by_norm.get(normalise_name(value), [])
            if len(matches) == 1:
                issues.append(_issue("category_in_list", "warning", path,
                                     f"'{value}' differs from the request's '{matches[0]}' only in case or punctuation; corrected", True))
                item = {**item, "category": matches[0]}
                value = matches[0]
            else:
                issues.append(_issue("category_in_list", "error", path,
                                     f"'{value}' is not in the request's category list", False))
        if value in seen:
            issues.append(_issue("category_duplicate", "warning", path, f"'{value}' listed twice; duplicate removed", True))
            continue
        seen.add(value)
        kept.append(item)
    out["classification"]["categories"] = kept


def _check_actions(out: dict[str, Any], allowed: list[str], issues: list[dict[str, Any]]) -> None:
    """Like categories, except that a name outside the list stays (a new action may be legitimate) and is
    only a warning. Underscores count as spaces: "send_reminder" is "Send Reminder"."""
    actions = out.get("suggested_actions")
    if not isinstance(actions, list):
        return
    def norm(name: str) -> str:
        return normalise_name(name.replace("_", " "))
    by_norm: dict[str, list[str]] = {}
    for name in allowed:
        by_norm.setdefault(norm(name), []).append(name)

    seen: set[str] = set()
    kept: list[Any] = []
    for i, item in enumerate(actions):
        path = f"$.suggested_actions[{i}].action"
        value = item.get("action") if isinstance(item, dict) else None
        if not isinstance(value, str):
            kept.append(item)
            continue
        if value not in allowed:
            matches = by_norm.get(norm(value), [])
            if len(matches) == 1:
                issues.append(_issue("action_in_list", "warning", path,
                                     f"'{value}' differs from the vocabulary's '{matches[0]}' only in case or punctuation; corrected", True))
                item = {**item, "action": matches[0]}
                value = matches[0]
            else:
                issues.append(_issue("action_in_list", "warning", path,
                                     f"'{value}' is not in the action vocabulary; kept as a new action, review it", False))
        if value in seen:
            issues.append(_issue("action_duplicate", "warning", path, f"'{value}' listed twice; duplicate removed", True))
            continue
        seen.add(value)
        kept.append(item)
    out["suggested_actions"] = kept


def _check_invoices(out: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    data = out.get("extracted_data")
    if not isinstance(data, dict):
        return
    invoices = data.get("invoice_numbers")
    if not isinstance(invoices, list):
        return
    clean: list[str] = []
    for i, raw in enumerate(invoices):
        path = f"$.extracted_data.invoice_numbers[{i}]"
        value = str(raw).strip().strip("()[]{},;:")
        if value != str(raw):
            issues.append(_issue("invoice_numbers", "warning", path, f"stray punctuation removed from '{raw}'", True))
        if _DATE_LIKE.match(value):
            issues.append(_issue("invoice_numbers", "warning", path, f"'{value}' is a date, not an invoice number; removed", True))
            continue
        if value in clean:
            issues.append(_issue("invoice_numbers", "warning", path, f"'{value}' listed twice; duplicate removed", True))
            continue
        clean.append(value)
    data["invoice_numbers"] = clean or None


def _check_draft(out: dict[str, Any], thread: dict[str, Any] | None, issues: list[dict[str, Any]]) -> None:
    draft = out.get("draft_response")
    if not isinstance(draft, dict):
        return
    if draft.get("requires_human_review") is not True:
        draft["requires_human_review"] = True
        issues.append(_issue("requires_human_review", "warning", "$.draft_response.requires_human_review",
                             "must always be true; set to true", True))
    body = draft.get("draft_body") or ""
    first_line = body.strip().split("\n", 1)[0] if body.strip() else ""

    if thread:
        latest = thread.get("latest_message") or {}
        ours = latest.get("greeting_name")
        reply_to = thread.get("reply_to_name")
        if ours and _surname(ours) and _surname(ours) in first_line and (not reply_to or _surname(reply_to) != _surname(ours)):
            issues.append(_issue("greeting_matches_reply_to", "warning", "$.draft_response.draft_body",
                                 f"the draft greets '{ours}', who is the AR team member the customer wrote to", False))
        elif reply_to and _surname(reply_to) and _surname(reply_to) not in first_line:
            issues.append(_issue("greeting_matches_reply_to", "warning", "$.draft_response.draft_body",
                                 f"the draft does not greet '{reply_to}', the person to reply to", False))

    if _PROMISES_DOCUMENT.search(body) and not _PLACEHOLDER.search(body):
        issues.append(_issue("attachment_placeholder", "warning", "$.draft_response.draft_body",
                             "the draft promises a document but has no '[... attached]' placeholder", False))


def _surname(name: str) -> str:
    tokens = _HONORIFIC.sub("", name.strip()).split()
    return tokens[-1] if tokens else ""


def validate_output(output: dict[str, Any], categories: list[Any], thread: dict[str, Any] | None = None) -> dict[str, Any]:
    out = copy.deepcopy(output)
    issues: list[dict[str, Any]] = []
    _check_categories(out, _category_names(categories), issues)
    _check_invoices(out, issues)
    _check_draft(out, thread, issues)
    return {
        "schema_version": SCHEMA_VERSION,
        "valid": not any(i["severity"] == "error" and not i["corrected"] for i in issues),
        "output": out,
        "issues": issues,
    }

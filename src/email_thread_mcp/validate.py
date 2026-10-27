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

from .thread import _GREETING

SCHEMA_VERSION = "1.0"

_DATE_LIKE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")
_PROMISES_DOCUMENT = re.compile(
    r"\b(send|attach|resend|forward|provide)\b[^.\n]{0,60}\b(copy|copies|invoice|statement|proof of delivery|pod|credit note)\b",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\[[^\]]*attached\]", re.IGNORECASE)
_MARKUP_TAG = re.compile(r"</?[A-Za-z][\w-]*>")
_REPLY_PREFIXES = re.compile(r"^((?:re|aw|sv|antw)\s*:\s*)(?:(?:re|aw|sv|antw)\s*:\s*)+", re.IGNORECASE)
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


def _allowed_categories(categories: list[Any]) -> list[str] | list[int]:
    """The values a category may take. A request whose objects carry an `id` and no `name` is matched
    by id (the classifier emits the integer); otherwise by name, as before 0.6.0."""
    if categories and all(isinstance(c, dict) and "name" not in c for c in categories):
        ids = [c.get("id") for c in categories]
        if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ValueError("every category without a name needs an integer id")
        return ids
    try:
        return _category_names(categories)
    except KeyError:
        raise ValueError("every category needs a name, or every category an integer id and no name") from None


def _check_category_ids(cats: list[Any], allowed: list[int], issues: list[dict[str, Any]]) -> list[Any]:
    """Ids are compared exactly. The one safe correction is a digit string naming an allowed id
    ("36" -> 36); there is no near match for a number, so anything else is an error."""
    allowed_set = set(allowed)
    seen: set[int] = set()
    kept: list[Any] = []
    for i, item in enumerate(cats):
        path = f"$.classification.categories[{i}].category"
        value = item.get("category") if isinstance(item, dict) else None
        if isinstance(value, str) and value.strip().isdigit() and int(value) in allowed_set:
            issues.append(_issue("category_in_list", "warning", path,
                                 f"'{value}' is the id {int(value)} written as a string; corrected", True))
            value = int(value)
            item = {**item, "category": value}
        if not isinstance(value, int) or isinstance(value, bool):
            issues.append(_issue("category_in_list", "error", path, "category is not an integer id", False))
            kept.append(item)
            continue
        if value not in allowed_set:
            issues.append(_issue("category_in_list", "error", path,
                                 f"{value} is not an id in the request's category list", False))
        if value in seen:
            issues.append(_issue("category_duplicate", "warning", path, f"{value} listed twice; duplicate removed", True))
            continue
        seen.add(value)
        kept.append(item)
    return kept


def _check_categories(out: dict[str, Any], allowed: list[str] | list[int], issues: list[dict[str, Any]]) -> None:
    cats = (out.get("classification") or {}).get("categories")
    if not isinstance(cats, list):
        issues.append(_issue("category_in_list", "error", "$.classification.categories", "missing categories list", False))
        return
    ids = [a for a in allowed if isinstance(a, int)]
    if ids:
        out["classification"]["categories"] = _check_category_ids(cats, ids, issues)
        return
    allowed = [a for a in allowed if isinstance(a, str)]
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


def _check_language(out: dict[str, Any], expected: str, issues: list[dict[str, Any]]) -> None:
    """Both language fields must name the requested response language. A mismatch is reported, never
    relabelled: a draft written in another language would only be hidden by a corrected label."""
    want = expected.strip().casefold()
    draft = out.get("draft_response")
    fields = [("$.language", out.get("language"))]
    if isinstance(draft, dict):
        fields.append(("$.draft_response.language", draft.get("language")))
    for path, value in fields:
        if not isinstance(value, str) or value.strip().casefold() != want:
            issues.append(_issue("language_matches_preference", "error", path,
                                 f"language is {value!r}, the request asked for '{want}'", False))


def _format_new_action(name: str) -> str:
    """A new action in the vocabulary's form: underscores as spaces, every word starting uppercase.
    Only first letters change, so "AR" or "PDF" stay as written."""
    words = name.replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _check_actions(out: dict[str, Any], allowed: list[str], issues: list[dict[str, Any]]) -> None:
    """Like categories, except that a name outside the list stays (a new action may be legitimate) and is
    only a warning. Underscores count as spaces: "send_reminder" is "Send Reminder". A new action is
    also put in the vocabulary's form, so no underscore or lowercase word reaches the user."""
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
                formatted = _format_new_action(value)
                if formatted != value:
                    issues.append(_issue("action_format", "warning", path,
                                         f"'{value}' rewritten as '{formatted}': no underscores, every word starts uppercase", True))
                    item = {**item, "action": formatted}
                    value = formatted
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
    subject = draft.get("draft_subject")
    if isinstance(subject, str):
        cleaned = _REPLY_PREFIXES.sub(lambda m: m.group(1).strip() + " ", " ".join(_MARKUP_TAG.sub("", subject).split()))
        if cleaned != subject:
            draft["draft_subject"] = cleaned
            issues.append(_issue("draft_subject_markup", "warning", "$.draft_response.draft_subject",
                                 f"removed markup tags, line breaks or a repeated reply prefix from {subject!r}", True))
    missing = [k for k in ("draft_subject", "draft_body") if not isinstance(draft.get(k), str) or not draft[k].strip()]
    for key in missing:
        issues.append(_issue("draft_missing", "error", f"$.draft_response.{key}",
                             f"{key} is empty; the drafting step produced no usable answer", False))
    if "draft_body" in missing:
        return
    body = draft["draft_body"]
    first_line = body.strip().split("\n", 1)[0]

    if thread:
        latest = thread.get("latest_message") or {}
        ours = latest.get("greeting_name")
        reply_to = thread.get("reply_to_name")
        address = thread.get("reply_to_greeting")
        fixed = _correct_greeting(first_line, address, reply_to)
        if fixed is not None:
            draft["draft_body"] = body = body.replace(first_line, fixed, 1)
            issues.append(_issue("greeting_matches_reply_to", "warning", "$.draft_response.draft_body",
                                 f"the greeting {first_line!r} does not name {address!r}; replaced with {fixed!r}", True))
        elif ours and _surname(ours) and _surname(ours) in first_line and (not reply_to or _surname(reply_to) != _surname(ours)):
            issues.append(_issue("greeting_matches_reply_to", "warning", "$.draft_response.draft_body",
                                 f"the draft greets '{ours}', who is the AR team member the customer wrote to", False))
        elif reply_to and _name_tokens(reply_to) and not _name_tokens(reply_to) & _name_tokens(first_line):
            issues.append(_issue("greeting_matches_reply_to", "warning", "$.draft_response.draft_body",
                                 f"the draft does not greet '{reply_to}', the person to reply to", False))

    if _PROMISES_DOCUMENT.search(body) and not _PLACEHOLDER.search(body):
        issues.append(_issue("attachment_placeholder", "warning", "$.draft_response.draft_body",
                             "the draft promises a document but has no '[... attached]' placeholder", False))


def _name_tokens(name: str) -> set[str]:
    return set(re.findall(r"\w+", normalise_name(_HONORIFIC.sub("", name.strip()))))


def _correct_greeting(first_line: str, address: Any, reply_to: Any) -> str | None:
    """The greeting line with its name replaced by `address`, or None when it needs no correction.

    Only a greeting that shares no name part with the person to reply to is replaced, so a draft
    that greets them by first name ("Dear Eva,") is kept as written.
    """
    if not isinstance(address, str) or not address.strip():
        return None
    m = _GREETING.match(first_line.strip())
    if not m or not m.group("name").strip():
        return None
    greeted = _name_tokens(m.group("name"))
    known = _name_tokens(address) | (_name_tokens(reply_to) if isinstance(reply_to, str) else set())
    if greeted & known:
        return None
    line = first_line.strip()
    start, end = m.span("name")
    return line[:start] + address.strip() + line[end:]


def _surname(name: str) -> str:
    tokens = _HONORIFIC.sub("", name.strip()).split()
    return tokens[-1] if tokens else ""


def validate_output(
    output: dict[str, Any],
    categories: list[Any],
    thread: dict[str, Any] | None = None,
    expected_language: str | None = None,
) -> dict[str, Any]:
    out = copy.deepcopy(output)
    issues: list[dict[str, Any]] = []
    _check_categories(out, _allowed_categories(categories), issues)
    if expected_language:
        _check_language(out, expected_language, issues)
    _check_invoices(out, issues)
    _check_draft(out, thread, issues)
    return {
        "schema_version": SCHEMA_VERSION,
        "valid": not any(i["severity"] == "error" and not i["corrected"] for i in issues),
        "output": out,
        "issues": issues,
    }

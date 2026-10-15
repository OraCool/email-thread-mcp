"""Split an inbound email body into the latest message and its quoted history.

Output shape: contracts/thread.schema.json (schema_version 1.1).

The body arrives as raw HTML (Outlook, Gmail, Apple Mail) or plain text, newest message on top.
It is converted to text with line structure kept, then cut at every quote boundary:

- a mail client's HTML quote marker (see html_text.QUOTE_MARKER) - works in any UI language,
- a header block of known labels: a `From:` line followed within a few lines by `Sent:`/`Date:`,
- any `label: value` block that starts with an address and holds a date with a time - unknown languages,
- `-----Original Message-----` and forwarded-message separators,
- attribution lines (`On <date>, <name> wrote:`, `Am <date> schrieb <name>:`, ...).

Words per language live in vocab.py. A missed quote boundary is the harmful failure (old history read
as the new message), so boundaries never depend on words alone; names and dates degrade to null.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import dateparser

from . import vocab
from .html_text import QUOTE_MARKER, html_to_text, looks_like_html, normalise_text, strip_invisible

SCHEMA_VERSION = "1.1"

# Bounds on attacker-sized input: the body is customer-controlled text.
_MAX_RAW_CHARS = 2_000_000
_MAX_TEXT_CHARS = 300_000


def _alternation(words: list[str]) -> str:
    return "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in sorted(words, key=lambda w: (-len(w), w)))


_LABELS = vocab.header_labels()
_LABEL_LANGUAGES = vocab.label_languages()
_FROM_LABELS = [label for label, key in _LABELS.items() if key == "from"]
_SENT_LABELS = [label for label, key in _LABELS.items() if key == "sent"]

_HEADER_FROM = re.compile(rf"^({_alternation(_FROM_LABELS)})\s*:\s*(?P<value>.*)$", re.IGNORECASE)
_HEADER_DATE = re.compile(rf"^({_alternation(_SENT_LABELS)})\s*:", re.IGNORECASE)
_HEADER_LINE = re.compile(rf"^(?P<key>{_alternation(list(_LABELS))})\s*:\s*(?P<value>.*)$", re.IGNORECASE)
_SEPARATOR = re.compile(rf"^-{{2,}}\s*({_alternation(vocab.all_words('separators'))})\s*-{{2,}}$", re.IGNORECASE)
_WROTE_VERB = re.compile(rf"(?<!\w)({_alternation(vocab.all_words('wrote'))})(?!\w)", re.IGNORECASE)
_HEADER_LOOKAHEAD = 5
_MIN_KNOWN_HEADERS = 3

# Any-language header line: a label of 1-3 words of letters, then a value.
_LABEL_LINE = re.compile(r"^(?P<label>[^\W\d_]+(?:[ .'-][^\W\d_]+){0,2})\s*:\s*(?P<value>\S.*)$")
_LABEL_MAX = 25
_GENERIC_HEADERS_MIN, _GENERIC_HEADERS_MAX = 3, 8
_TIME = re.compile(r"(?<!\d)\d{1,2}[:.]\d{2}(?!\d)")
_NUMERIC_DATE = re.compile(r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4},?(\s+\d{1,2}[:.]\d{2})?")
_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

_GREETING = re.compile(
    rf"^({_alternation(vocab.all_words('greetings'))})(?!\w)[\s,]*(?P<name>[^,!:]*?)\s*[,!:]?$",
    re.IGNORECASE,
)
_HONORIFIC = re.compile(rf"^({_alternation(vocab.all_words('honorifics'))})\.?(\s+|$)", re.IGNORECASE)
_CLOSING = re.compile(
    rf"^({_alternation(vocab.all_words('closings'))})(?!\w)\s*[,.!-]?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_NAME_PARTICLES = {"von", "van", "der", "de", "da", "di", "le", "la", "du", "del", "den", "ten", "zu"}
_NOT_NAME_WORDS = {
    "accounts", "payable", "receivable", "department", "team", "finance", "procurement", "gmbh", "ltd",
    "inc", "ag", "ab", "llc", "co", "corp", "company", "credit", "collections", "manager", "sent",
    "from", "iphone", "office", "support", "customer", "service", "bv", "nv", "sa", "srl", "spa", "sas",
    "sarl", "oy", "oyj", "aps", "kg", "se",
}
# Bounded parts keep the search linear on long runs of word characters without an '@'.
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}")
_NAME_BEFORE_ADDRESS = re.compile(r"((?:[^\W\d_][\w.'’-]*\s+){0,4})(<[^<>\s]+@[^<>\s]+>)")
# Words that sit between an attribution's date and the sender's name ("at 9:15 AM Anna Keller").
_ATTRIBUTION_FILLERS = {"am", "pm", "at", "um", "à", "om", "alle", "a", "las", "às", "o", "kl", "klo", "v"}


@dataclass
class Message:
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    role: str = "unknown"
    greeting_name: str | None = None
    signer_name: str | None = None
    signature: str | None = None
    text_without_signature: str = ""
    sent_at: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------------------------- #

_OUTLOOK_BOLD_FROM = re.compile(rf"<b>\s*({_alternation(_FROM_LABELS)}):\s*</b>", re.IGNORECASE)


def detect_format(raw: str, quoted_messages: int) -> str:
    if looks_like_html(raw):
        if re.search(r'id="divRplyFwdMsg"|border-top:\s*solid', raw, re.IGNORECASE) or _OUTLOOK_BOLD_FROM.search(raw):
            return "outlook-html"
        if "gmail_quote" in raw:
            return "gmail-html"
        if re.search(r'<blockquote[^>]*type="cite"', raw, re.IGNORECASE):
            return "apple-html"
        return "html-unknown"
    return "plain-text" if quoted_messages else "plain-text-no-quotes"


def _is_wrote(line: str) -> bool:
    """Attribution line: a 'wrote' verb plus an address or a year, ending the line (any language)."""
    if len(line) > 300 or not _WROTE_VERB.search(line):
        return False
    if not (_EMAIL.search(line) or _YEAR.search(line)):
        return False
    return line.rstrip().endswith(":") or bool(_WROTE_VERB.search(line.rstrip()[-20:]))


def _is_marker_attribution(line: str) -> bool:
    """Line right after an HTML quote marker that ends in ':' and names an address: an attribution in a
    language whose 'wrote' verb is not in the vocabulary."""
    return len(line) <= 300 and line.rstrip().endswith(":") and bool(_EMAIL.search(line))


def _known_header_block(lines: list[str], i: int) -> bool:
    if not _HEADER_FROM.match(lines[i]):
        return False
    window = lines[i : i + 1 + 2 * _HEADER_LOOKAHEAD]  # header lines may be separated by blanks
    if not any(_HEADER_DATE.match(w) for w in window[1:]):
        return False
    return sum(1 for w in window if _HEADER_LINE.match(w)) >= _MIN_KNOWN_HEADERS


def _label_line(line: str) -> re.Match[str] | None:
    m = _LABEL_LINE.match(line)
    return m if m and len(m.group("label")) <= _LABEL_MAX else None


def _generic_header_block(lines: list[str], i: int) -> list[int] | None:
    """Indexes of a `label: value` block starting at i, whatever the labels' language, or None.

    The first value must carry an address (the sender) and a later one a date with a time of day:
    every mail client's quote header has both, a remittance table in the body rarely does.
    """
    first = _label_line(lines[i])
    if not first or not _EMAIL.search(first.group("value")) or len(first.group("value")) > 150:
        return None
    found = [i]
    j = i + 1
    while j < len(lines) and len(found) < _GENERIC_HEADERS_MAX:
        if not lines[j]:
            j += 1
            continue
        if not _label_line(lines[j]):
            break
        found.append(j)
        j += 1
    if len(found) < _GENERIC_HEADERS_MIN:
        return None
    values = [_label_line(lines[k]).group("value") for k in found[1:]]  # type: ignore[union-attr]
    if not any(_TIME.search(v) and "@" not in v and parse_sent(v) for v in values):
        return None
    return found


def _boundaries(lines: list[str]) -> list[int]:
    """Line indexes where a quoted message starts (its marker, header, separator or attribution line)."""
    starts: list[int] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        is_start = (
            line == QUOTE_MARKER
            or bool(_SEPARATOR.match(line))
            or _is_wrote(line)
            or _known_header_block(lines, i)
            or _generic_header_block(lines, i) is not None
            or (i + 1 < len(lines) and _is_wrote(f"{line} {lines[i + 1]}"))  # attribution wrapped over two lines
        )
        if not is_start:
            i += 1
            continue
        starts.append(i)
        # Marker, separator, attribution and header block of one quoted message: one boundary, not several.
        i += 1
        while i < len(lines):
            block = _generic_header_block(lines, i)
            if block:
                i = block[-1] + 1
            elif (
                not lines[i]
                or lines[i] == QUOTE_MARKER
                or _SEPARATOR.match(lines[i])
                or _HEADER_LINE.match(lines[i])
                or _is_wrote(lines[i])
                or _is_marker_attribution(lines[i])
            ):
                i += 1
            else:
                break
    return starts


def _attribution_from(line: str) -> str | None:
    """Sender out of an attribution line: 'Name <address>', else the address, else the name before the verb."""
    m = _NAME_BEFORE_ADDRESS.search(line)
    if m:
        tokens = m.group(1).split()
        while tokens and (
            tokens[0].lower() in _ATTRIBUTION_FILLERS or not tokens[0][0].isupper() or _WROTE_VERB.fullmatch(tokens[0])
        ):
            tokens.pop(0)
        return " ".join([*tokens, m.group(2)])
    m = _EMAIL.search(line)
    if m:
        return m.group(0)
    verb = _WROTE_VERB.search(line)
    before = line[: verb.start()] if verb else line
    name = before.rsplit(",", 1)[-1].strip(" :")
    return name or None


def _attribution_date(line: str, sender: str | None) -> str | None:
    text = line.replace(sender, " ") if sender else line
    text = _WROTE_VERB.sub(" ", text).strip(" :,")
    words = text.split()
    for skip in range(3):  # leading 'On' / 'Am' / 'Le' / 'W dniu' ...
        if (value := parse_sent(" ".join(words[skip:]).strip(" ,"))) is not None:
            return value
    numeric = _NUMERIC_DATE.search(text)  # words of an unknown language around a numeric date
    return parse_sent(numeric.group(0)) if numeric else None


def _parse_quoted(block: list[str]) -> Message:
    headers: dict[str, str] = {}
    unknown_labels: list[str] = []
    sent_languages: list[str] | None = None
    attribution: str | None = None
    marker_only = True
    after_marker = False
    idx = 0
    while idx < len(block):
        line = block[idx]
        if not line or line == QUOTE_MARKER:
            after_marker = after_marker or line == QUOTE_MARKER
            idx += 1
            continue
        if _SEPARATOR.match(line):
            marker_only = False
            idx += 1
            continue
        if not headers and attribution is None:
            if _is_wrote(line) or (after_marker and _is_marker_attribution(line)):
                attribution = line
                idx += 1
                continue
            if idx + 1 < len(block) and _is_wrote(f"{line} {block[idx + 1]}"):
                attribution = f"{line} {block[idx + 1]}"
                idx += 2
                continue
        m = _HEADER_LINE.match(line)
        if m:
            label = m.group("key").lower()
            headers.setdefault(_LABELS[label], m.group("value").strip())
            if _LABELS[label] == "sent":
                sent_languages = _LABEL_LANGUAGES.get(label)
            idx += 1
            continue
        generic = _generic_header_block(block, idx)
        if generic:
            unknown_labels += _assign_generic_headers([block[k] for k in generic], headers)
            idx = generic[-1] + 1
            continue
        break

    if attribution is not None:
        sender = _attribution_from(attribution)
        if sender:
            headers.setdefault("from", sender)
        sent_at = _attribution_date(attribution, sender)
    else:
        sent_at = parse_sent(headers.get("sent"), sent_languages)

    message = Message(
        body=normalise_text("\n".join(re.sub(r"^(>\s?)+", "", line) for line in block[idx:])),
        headers=headers,
        sent_at=sent_at,
    )
    if unknown_labels:
        message.notes.append(f"header labels not recognised: {', '.join(unknown_labels)}")
    if headers.get("sent") and sent_at is None:
        message.notes.append(f"sent date not parsed: {headers['sent']!r}")
    if marker_only and not headers and attribution is None:
        message.notes.append("found by the HTML quote marker only: sender and date unknown")
    return message


def _assign_generic_headers(lines: list[str], headers: dict[str, str]) -> list[str]:
    """Map a header block's labels to from/to/sent/subject. Unknown labels are mapped by their value:
    the first address is the sender, the next the recipient, a date the sent time, the last line the
    subject. Returns the labels that were not in the vocabulary."""
    unknown: list[str] = []
    pairs = [(m.group("label"), m.group("value").strip()) for m in map(_label_line, lines) if m]
    for n, (label, value) in enumerate(pairs):
        key = _LABELS.get(label.lower())
        if key is None:
            unknown.append(label)
            if _EMAIL.search(value):
                key = "from" if "from" not in headers else "to" if "to" not in headers else "cc"
            elif "sent" not in headers and _TIME.search(value) and parse_sent(value):
                key = "sent"
            elif n == len(pairs) - 1:
                key = "subject"
            else:
                continue
        headers.setdefault(key, value)
    return unknown


# --------------------------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------------------------- #


def looks_like_name(text: str) -> bool:
    text = text.strip().strip(",.")
    if not text or len(text) > 50 or any(ch.isdigit() for ch in text) or "@" in text:
        return False
    tokens = text.replace("-", " ").split()
    if not 1 <= len(tokens) <= 4:
        return False
    if any(t.lower().strip(".,") in _NOT_NAME_WORDS for t in tokens):
        return False
    return all(t[0].isupper() or t.lower() in _NAME_PARTICLES for t in tokens)


def find_greeting(lines: list[str]) -> str | None:
    for line in lines[:3]:
        m = _GREETING.match(line)
        if m:
            name = m.group("name").strip()
            return name if name and looks_like_name(_HONORIFIC.sub("", name)) else None
    return None


def find_signoff(lines: list[str]) -> tuple[int | None, str | None, str | None]:
    """Return (index of the closing line, signer name, signature block) for the LAST sign-off."""
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if len(line) > 60:
            continue
        m = _CLOSING.match(line)
        if not m:
            continue
        rest = m.group("rest").strip()
        if rest and not looks_like_name(rest):
            continue  # "Thank you for your collaboration." is a sentence, not a sign-off
        after = [l for l in lines[idx + 1 : idx + 9] if l]
        signer = rest or (after[0] if after and looks_like_name(after[0]) else None)
        signature_lines = ([rest] if rest else []) + after
        return idx, signer, "\n".join(signature_lines) or None
    return None, None, None


def _annotate(message: Message, lines: list[str]) -> None:
    message.greeting_name = find_greeting(lines)
    idx, message.signer_name, message.signature = find_signoff(lines)
    message.text_without_signature = normalise_text("\n".join(lines[:idx] if idx is not None else lines))


# --------------------------------------------------------------------------------------------- #
# Roles, dates, reply-to
# --------------------------------------------------------------------------------------------- #


def _addresses(value: str | None) -> set[str]:
    return {m.group(0).lower() for m in _EMAIL.finditer(value or "")}


def assign_role(headers: dict[str, str], ar_mailboxes: list[str]) -> str:
    """recipient = written by the AR team (us); sender = written by the customer side."""
    if not ar_mailboxes:
        return "unknown"

    def is_ours(addresses: set[str]) -> bool:
        for entry in (e.lower().strip() for e in ar_mailboxes):
            if any(a == entry or (entry.startswith("@") and a.endswith(entry)) for a in addresses):
                return True
        return False

    if is_ours(_addresses(headers.get("from"))):
        return "recipient"
    if is_ours(_addresses(headers.get("to"))):
        return "sender"
    return "unknown"


# Outlook's English forms, parsed without dateparser's start-up cost.
_DATE_FORMATS = (
    "%A, %B %d, %Y %I:%M %p",
    "%A, %B %d, %Y %H:%M",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d %H:%M",
)
# Absolute dates only: a relative word ("yesterday") must not become today's date, and a missing year
# must not be filled in.
_MAX_DATE_CHARS = 100  # a header date is ~50 chars; dateparser's cost grows with the text
_DATEPARSER_CALLS = 50  # per parse_email_thread call: real threads need a few, crafted bodies thousands
_date_budget: ContextVar[list[int] | None] = ContextVar("_date_budget", default=None)  # [calls left, refused]
_date_cache: dict[tuple[str, tuple[str, ...] | None, str], str | None] = {}
_DATEPARSER_SETTINGS = {
    "PARSERS": ["timestamp", "absolute-time"],
    "REQUIRE_PARTS": ["day", "month", "year"],
    "RETURN_AS_TIMEZONE_AWARE": False,
}


def parse_sent(value: str | None, languages: list[str] | None = None) -> str | None:
    """A quote header's date as ISO 8601, in any of dateparser's languages; None when not parseable."""
    if not value:
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    if len(text) > _MAX_DATE_CHARS:
        return None
    order = "MDY" if re.search(r"\b[ap]\.?m\.?\b", text, re.IGNORECASE) else "DMY"
    key = (text, tuple(languages) if languages else None, order)
    if key in _date_cache:
        return _date_cache[key]
    budget = _date_budget.get()
    if budget is not None:
        if budget[0] <= 0:
            budget[1] += 1
            return None
        budget[0] -= 1
    try:
        parsed = dateparser.parse(text, languages=languages, settings={**_DATEPARSER_SETTINGS, "DATE_ORDER": order})  # type: ignore[arg-type]
    except Exception:  # dateparser raises assorted errors on odd input; a date is never worth failing the parse
        parsed = None
    if len(_date_cache) > 4096:
        _date_cache.clear()
    _date_cache[key] = parsed.isoformat() if parsed else None
    return _date_cache[key]


def resolve_reply_to_name(latest: Message, history_newest_first: list[Message]) -> str | None:
    """Who the draft reply should greet.

    Agreed order:
      1. the signer of the latest message;
      2. else the name OUR most recent quoted message greeted (the customer replied to it);
      3. else None - the drafter then uses a neutral greeting. Never guess a name.

    Step 2 trusts only a message positively identified as ours (role "recipient", which needs
    ar_mailboxes). With unknown roles the greeting could be our own colleague's name, so it is skipped.
    """
    if latest.signer_name:
        return latest.signer_name
    ours = next((m for m in history_newest_first if m.role == "recipient"), None)
    return ours.greeting_name if ours else None


def _name_tokens(name: str) -> list[str]:
    return [t.lower().strip(".,") for t in _HONORIFIC.sub("", name.strip()).split()]


def _has_honorific(name: str) -> bool:
    return bool(_HONORIFIC.match(name.strip())) and len(_name_tokens(name)) >= 1


def _same_person(greeting: str, full_name: str) -> bool:
    """'Mr. Berg' or 'Anna' (as greeted) against 'Jan Berg' / 'Anna Keller' (as signed)."""
    greeted, signed = _name_tokens(greeting), _name_tokens(full_name)
    if not greeted or not signed:
        return False
    if _has_honorific(greeting) or len(greeted) > 1:
        return greeted[-1] == signed[-1]
    return greeted[0] in (signed[0], signed[-1])


def resolve_reply_to_greeting(reply_to_name: str | None, history_newest_first: list[Message]) -> str | None:
    """The form of address for reply_to_name: how a quoted message of ours already greeted that person.

    'Jan Berg' becomes 'Mr. Berg' when an earlier message opened "Dear Mr. Berg,";
    otherwise the name stays as given. An honorific is never added that no message states.
    """
    if not reply_to_name:
        return None
    for m in history_newest_first:
        if m.role != "sender" and m.greeting_name and _has_honorific(m.greeting_name) and _same_person(m.greeting_name, reply_to_name):
            return m.greeting_name
    return reply_to_name


_SIGNATURE_LINES = 4


def _signature_line_ok(line: str) -> bool:
    """Name, title, department, company. Not contact data: phone, address, email, URL, or a disclaimer."""
    return (
        len(line) <= 60
        and not _EMAIL.search(line)
        and not re.search(r"https?://|www\.", line, re.IGNORECASE)
        and sum(ch.isdigit() for ch in line) < 5
    )


def _clean_signature(m: Message) -> str | None:
    lines = [l.strip() for l in (m.signature or "").split("\n") if l.strip()]
    if not lines:
        return None
    if lines[0] != m.signer_name:
        return m.signer_name
    kept = [lines[0]] + [l for l in lines[1:] if _signature_line_ok(l)]
    return "\n".join(kept[:_SIGNATURE_LINES])


def resolve_our_signature(latest: Message, history_newest_first: list[Message]) -> str | None:
    """The signature block the reply is signed with: that of our quoted messages.

    Ours is the newest quoted message from the AR team (role "recipient"), else one signed by the person
    the latest message greets (it wrote "Dear Ms. Weiss", so Clara Weiss's message is ours). A message
    known to be the customer's never counts. Of that person's quoted messages, the fullest block is used
    (a short follow-up is often signed with the name alone); the newest wins a tie. Contact lines are
    dropped; None means no such message.
    """
    ours = next((m for m in history_newest_first if m.role == "recipient" and m.signer_name and m.signature), None)
    if ours is None and latest.greeting_name:
        ours = next(
            (m for m in history_newest_first
             if m.role != "sender" and m.signer_name and m.signature and _same_person(latest.greeting_name, m.signer_name)),
            None,
        )
    if ours is None:
        return None
    blocks = [
        b for m in history_newest_first
        if m.role == ours.role and m.signer_name == ours.signer_name and (b := _clean_signature(m))
    ]
    return max(blocks, key=lambda b: b.count("\n"), default=None)


# --------------------------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------------------------- #


def parse_email_thread(body: str, ar_mailboxes: list[str] | None = None) -> dict[str, Any]:
    budget = [_DATEPARSER_CALLS, 0]
    token = _date_budget.set(budget)
    try:
        result = _parse_email_thread(body, ar_mailboxes)
    finally:
        _date_budget.reset(token)
    if budget[1]:
        result["parse_info"]["warnings"].append("date parsing limit reached: some dates left null")
    return result


def _parse_email_thread(body: str, ar_mailboxes: list[str] | None) -> dict[str, Any]:
    warnings: list[str] = []
    raw = (body or "").replace("\x00", "")  # NUL is the quote marker's delimiter; the body cannot forge one
    if len(raw) > _MAX_RAW_CHARS:
        raw = raw[:_MAX_RAW_CHARS]
        warnings.append(f"body truncated to {_MAX_RAW_CHARS} characters before parsing")
    if looks_like_html(raw):
        text, hidden_chars = html_to_text(raw)
        if hidden_chars:
            warnings.append(f"hidden HTML text removed: {hidden_chars} characters a reader would not see")
    else:
        text = normalise_text(raw)
    text, invisible = strip_invisible(text)
    if invisible:
        warnings.append(f"invisible characters removed: {invisible} (zero-width, bidirectional or control)")
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]
        warnings.append(f"text truncated to {_MAX_TEXT_CHARS} characters; the end of the quoted history is lost")
    lines = text.split("\n")
    starts = _boundaries(lines)

    latest_lines = [l for l in lines[: starts[0]] if l and l != QUOTE_MARKER] if starts else [l for l in lines if l and l != QUOTE_MARKER]
    latest = Message(body=normalise_text("\n".join(latest_lines)))
    _annotate(latest, latest_lines)

    quoted: list[Message] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        msg = _parse_quoted(lines[start:end])
        msg.body = normalise_text("\n".join(l for l in msg.body.split("\n") if l != QUOTE_MARKER))
        if not msg.body:
            warnings.append(f"quoted message {n + 1} has no body text")
            continue
        msg_lines = [l for l in msg.body.split("\n") if l]
        _annotate(msg, msg_lines)
        msg.role = assign_role(msg.headers, ar_mailboxes or [])
        warnings += [f"quoted message {n + 1}: {note}" for note in msg.notes]
        quoted.append(msg)

    if not starts:
        warnings.append("no quote marker found: whole body treated as the latest message")
    if latest.signer_name is None:
        warnings.append("latest message: no signer name found in the sign-off")
    if not latest.text_without_signature:
        warnings.append("latest message has no text besides greeting/signature")

    reply_to_name = resolve_reply_to_name(latest, quoted)
    return {
        "schema_version": SCHEMA_VERSION,
        "latest_message": {
            "text": latest.text_without_signature or latest.body,
            "greeting_name": latest.greeting_name,
            "signer_name": latest.signer_name,
            "signature": latest.signature,
        },
        "thread_context": [
            {
                "role": m.role,
                "from": m.headers.get("from"),
                "to": m.headers.get("to"),
                "sent_at": m.sent_at,
                "subject": m.headers.get("subject"),
                "greeting_name": m.greeting_name,
                "signer_name": m.signer_name,
                "body": m.body,
            }
            for m in reversed(quoted)  # contract: oldest first
        ],
        "reply_to_name": reply_to_name,
        "reply_to_greeting": resolve_reply_to_greeting(reply_to_name, quoted),
        "our_signature": resolve_our_signature(latest, quoted),
        "parse_info": {
            "format": detect_format(raw, len(quoted)),
            "quoted_messages": len(quoted),
            "warnings": warnings,
        },
    }

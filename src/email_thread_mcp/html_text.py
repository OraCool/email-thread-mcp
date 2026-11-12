"""HTML email body -> plain text that keeps the line structure.

Line structure matters here: quote headers (`From:` / `Sent:` lines), greetings and sign-off blocks
are all detected line by line, so block elements and `<br>` become line breaks rather than spaces.

Text a human reader cannot see is dropped: it is the usual way to put instructions in an email for the
LLM but not for the reviewer. That covers CSS hiding (inline or by a <style> rule on a tag, class or id:
display/visibility, a font under 2px, opacity under 0.1, zero size with overflow hidden, off-screen
offsets, clipping, scale(0), transparent text), <template>/<noscript>, and invisible characters
(zero-width, bidirectional controls, Unicode tag characters, variation selectors). A body that is
hidden as a whole yields no text at all: it fails closed, with a note for human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section", "table", "tr", "ul",
}
_CELL_TAGS = {"td", "th"}
# A cell this small is data (an invoice number, a date, an amount) and its row becomes one line. A larger
# one is layout: Outlook may put a whole message in a single cell, and joining that would ruin it.
_SHORT_CELL_LINES = 3
_SHORT_CELL_CHARS = 200
_SKIP_TAGS = {"head", "script", "style", "title"}
_HIDDEN_TAGS = {"template", "noscript"}  # not rendered by a mail client; counted as hidden text
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Zero-width, bidirectional-control and other invisible format characters, C0 controls but tab/newline,
# Unicode tag characters and variation selectors (both can smuggle text an LLM reads and a person does not see).
_INVISIBLE = re.compile(
    "[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\x01-\x08\x0b\x0c\x0e-\x1f\x7f"
    "\ufe00-\ufe0f\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)

# --------------------------------------------------------------------------------------------- #
# CSS: is a declaration block hiding its element?
# --------------------------------------------------------------------------------------------- #

_LENGTH = re.compile(r"^(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(px|pt|em|rem|%)?$")  # one number, no overlapping parts
_PX_PER_UNIT = {None: 1.0, "px": 1.0, "pt": 4 / 3, "em": 16.0, "rem": 16.0, "%": 0.16}
_MIN_FONT_PX = 2.0
_MIN_OPACITY = 0.1
_OFF_SCREEN_PX = -500.0
_SIZE_PROPS = ("height", "max-height", "width", "max-width")
_MAX_HIDING_RULES = 200


def _px(value: str | None) -> float | None:
    m = _LENGTH.match(value) if value and len(value) <= 40 else None
    return float(m.group(1)) * _PX_PER_UNIT[m.group(2)] if m else None


# The pre-0.10.2 check, kept as a floor: a substring match on the raw text, so no style it hid can pass
# now, however it is written. Its zero is "0+(.0*)?|.0+" rather than "0*.?0+", whose parts overlap.
_LEGACY_HIDDEN = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all"
    r"|(?<![\w-])font-size\s*:\s*(?:0+(?:\.0*)?|\.0+)(px|pt|em|rem|%)?\s*(;|!|$)"
    r"|(?<![\w-])opacity\s*:\s*(?:0+(?:\.0*)?|\.0+)\s*(;|!|$)",
    re.IGNORECASE,
)
_IMPORTANT = re.compile(r"!\s*important\s*$")  # CSS allows space between '!' and 'important'


def _declarations(block: str) -> dict[str, list[str]]:
    """Every value each property is given, '!important' removed.

    All values are kept, not the last one: a browser drops an invalid later value ("display:none;
    display:x" is still hidden) and lets '!important' win over a later one, so the check fails closed
    and treats an element as hidden when any of its declarations hides it.
    """
    props: dict[str, list[str]] = {}
    for decl in block.split(";"):
        key, sep, value = decl.partition(":")
        if sep:
            props.setdefault(key.strip().lower(), []).append(_IMPORTANT.sub("", value.strip().lower()).strip())
    return props


def _opacity(value: str) -> float | None:
    m = _LENGTH.match(value) if value and len(value) <= 40 else None
    if not m or m.group(2) not in (None, "%"):
        return None
    return float(m.group(1)) / (100 if m.group(2) == "%" else 1)


def _all_zero(text: str) -> bool:
    numbers = re.findall(r"-?\d*\.?\d+", text[:100])
    return bool(numbers) and all(float(n) == 0 for n in numbers)


def style_hides(block: str) -> bool:
    """True when a CSS declaration block makes its element's text invisible to a reader.

    Fails closed twice over: a property counts as hiding when any of its values hides (see
    _declarations), and the block is read both as written and with /*...*/ comments removed, since a
    '/*' inside a quoted string or url(...) is not a comment ('content:"/*";display:none' hides).
    """
    if _LEGACY_HIDDEN.search(block):
        return True
    stripped = _strip_comments(block)
    return _block_hides(block) or (stripped != block and _block_hides(stripped))


def _block_hides(block: str) -> bool:
    p = _declarations(block)

    def values(key: str) -> list[str]:
        return p.get(key, [])

    def lengths(key: str) -> list[float]:
        return [v for v in map(_px, values(key)) if v is not None]

    if (
        "none" in values("display")
        or {"hidden", "collapse"} & set(values("visibility"))
        or "all" in values("mso-hide")
        or "transparent" in values("color")
        or any(v < _MIN_FONT_PX for v in lengths("font-size"))
        or any(o is not None and o < _MIN_OPACITY for o in map(_opacity, values("opacity")))
    ):
        return True
    if {"hidden", "clip"} & set(values("overflow")) and any(0 in lengths(k) for k in _SIZE_PROPS):
        return True
    offsets = ["text-indent", "margin-left", "margin-top"]
    if {"absolute", "fixed", "relative"} & set(values("position")):
        offsets += ["left", "top"]
    if any(v <= _OFF_SCREEN_PX for k in offsets for v in lengths(k)):
        return True
    if any(c.startswith("rect(") and _all_zero(c) for c in values("clip")):
        return True
    if any(re.match(r"^(inset\(\s*(50|100)%|circle\(\s*0)", c) for c in values("clip-path")):
        return True
    return any(re.match(r"^scale[xy]?\(", t) and _all_zero(t.split("(", 1)[1]) for t in values("transform"))


# A selector's last compound: optional tag, then .class / #id parts. Each part starts with '.' or '#',
# which the name class excludes, so the match is linear.
_SIMPLE_COMPOUND = re.compile(r"^(?P<tag>[a-z][a-z0-9]*)?(?P<parts>(?:[.#][\w-]+)*)$", re.IGNORECASE)


@dataclass(frozen=True)
class _HidingRule:
    tag: str | None
    classes: frozenset[str]
    id: str | None

    def matches(self, tag: str, classes: set[str], id_: str | None) -> bool:
        return (
            (self.tag is None or self.tag == tag)
            and self.classes <= classes
            and (self.id is None or self.id == id_)
        )


def _strip_comments(css: str) -> str:
    out, i = [], 0
    while (start := css.find("/*", i)) != -1:
        out.append(css[i:start])
        end = css.find("*/", start + 2)
        i = len(css) if end == -1 else end + 2
    out.append(css[i:])
    return "".join(out)


def hiding_rules(css: str) -> tuple[list[_HidingRule], int]:
    """Rules of a style sheet that hide what they select, and how many hiding selectors could not be
    applied (attribute or pseudo selectors, '*', or a rule inside a conditional @media/@supports block).

    Only a selector's last compound is matched, so ".a .b {display:none}" hides every ".b": text is
    dropped rather than let through. An @media block for a media type alone ("screen", "all") applies.
    """
    stripped = _strip_comments(css)
    if stripped == css:
        return _sheet_rules(css)
    # As for inline styles, read the sheet both ways: a '/*' in a quoted string is not a comment.
    rules_a, unresolved_a = _sheet_rules(stripped)
    rules_b, unresolved_b = _sheet_rules(css)
    return _bounded(rules_a + rules_b, max(unresolved_a, unresolved_b))


def _bounded(rules: list[_HidingRule], unresolved: int) -> tuple[list[_HidingRule], int]:
    unique = list(dict.fromkeys(rules))
    if len(unique) > _MAX_HIDING_RULES:  # every element is checked against every rule: keep that bounded
        unresolved += len(unique) - _MAX_HIDING_RULES
        unique = unique[:_MAX_HIDING_RULES]
    return unique, unresolved


def _sheet_rules(css: str) -> tuple[list[_HidingRule], int]:
    rules: list[_HidingRule] = []
    unresolved = 0
    stack: list[tuple[str, bool]] = []  # (prelude, is a conditional at-rule)
    conditional = 0  # open conditional blocks: kept as a count so each rule costs O(1), whatever the nesting
    seg = 0
    for brace in re.finditer(r"[{}]", css):
        i = brace.start()
        if brace.group() == "{":
            prelude = css[seg:i].strip()
            cond = prelude.startswith("@") and ("(" in prelude or not prelude.lower().startswith("@media"))
            stack.append((prelude, cond))
            conditional += cond
        elif stack:
            prelude, cond = stack.pop()
            conditional -= cond
            if not prelude.startswith("@") and style_hides(css[seg:i]):
                for selector in prelude.split(","):
                    last = re.split(r"[\s>+~]+", selector.strip())[-1] if selector.strip() else ""
                    m = _SIMPLE_COMPOUND.match(last)
                    if conditional or not last or not m or not (m.group("tag") or m.group("parts")):
                        unresolved += 1
                        continue
                    # Lowercase: quirks mode (an email without a doctype) matches class and id in any case.
                    parts = re.findall(r"[.#][\w-]+", m.group("parts").lower())
                    ids = [x[1:] for x in parts if x[0] == "#"]
                    rules.append(_HidingRule(
                        tag=m.group("tag").lower() if m.group("tag") else None,
                        classes=frozenset(x[1:] for x in parts if x[0] == "."),
                        id=ids[0] if len(ids) == 1 else None,
                    ))
                    if len(ids) > 1:
                        rules.pop()
                        unresolved += 1
        seg = i + 1
    return _bounded(rules, unresolved)


class _StyleCollector(HTMLParser):
    """The text of every <style> element, wherever it sits in the body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self.css: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += tag == "style"

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.css.append(data)

# Line emitted where a mail client marks the start of quoted history. The markup is the same in every
# UI language, so the thread parser can split on it even when it knows none of the header words.
QUOTE_MARKER = "\x00quote\x00"


def _is_quote_marker(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
    a = {k.lower(): (v or "") for k, v in attrs}
    classes = a.get("class", "").split()
    return (
        a.get("id") == "divRplyFwdMsg"  # Outlook
        or "gmail_quote" in classes  # Gmail
        or "moz-cite-prefix" in classes  # Thunderbird
        or (tag == "blockquote" and a.get("type", "").lower() == "cite")  # Apple Mail, Thunderbird
    )


_RuleIndex = dict[tuple[str, str | None], list[_HidingRule]]


def _index_rules(rules: list[_HidingRule]) -> _RuleIndex:
    """Each rule under one key it requires: its id, else one of its classes, else its tag (or any)."""
    index: _RuleIndex = {}
    for r in rules:
        key = ("id", r.id) if r.id else ("class", min(r.classes)) if r.classes else ("tag", r.tag)
        index.setdefault(key, []).append(r)
    return index


def _is_hidden(tag: str, attrs: list[tuple[str, str | None]], rules: _RuleIndex) -> bool:
    """Every occurrence of an attribute counts: a duplicate style/class/id cannot mask the hiding one."""
    if tag in _HIDDEN_TAGS:
        return True
    if any(k.lower() == "hidden" or (k.lower() == "style" and v and style_hides(v)) for k, v in attrs):
        return True
    if not rules:
        return False
    classes = {c for k, v in attrs if k.lower() == "class" and v for c in v.lower().split()}
    ids = {v.lower() for k, v in attrs if k.lower() == "id" and v}
    keys = [("id", i) for i in ids] + [("class", c) for c in classes] + [("tag", tag), ("tag", None)]
    return any(
        r.matches(tag, classes, i)
        for key in keys for r in rules.get(key, ())
        for i in (ids or {None})
    )


class _TextExtractor(HTMLParser):
    def __init__(self, rules: list[_HidingRule] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._rules = _index_rules(rules or [])
        # A hidden element is left at its own closing tag: only that tag name is counted, so unclosed
        # <p>/<li> inside it cannot keep the rest of the email hidden.
        self._hidden_tag: str | None = None
        self._hidden_depth = 0
        self.hidden_chars = 0
        # Open cells (each collects its own text), open rows (cells done so far, and how many cells were
        # open when the row started) and open tables (how many rows and cells were open at their start).
        self._cells: list[list[str]] = []
        self._rows: list[tuple[list[str], int]] = []
        self._tables: list[tuple[int, int]] = []

    def _out(self) -> list[str]:
        return self._cells[-1] if self._cells else self._parts

    def _close_cell(self) -> None:
        text = normalise_text("".join(self._cells.pop()))
        if self._rows and len(self._cells) == self._rows[-1][1]:
            self._rows[-1][0].append(text)
        elif text:
            self._out().append(f"\n{text}\n")

    def _close_row(self) -> None:
        while self._cells and len(self._cells) > self._rows[-1][1]:
            self._close_cell()
        cells, _ = self._rows.pop()
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            return
        short = all(
            QUOTE_MARKER not in c and c.count("\n") < _SHORT_CELL_LINES and len(c) <= _SHORT_CELL_CHARS for c in cells
        )
        if short:
            out = self._out()
            line = " | ".join(c.replace("\n", " ") for c in cells)
            # Rows of one table stay together: no blank line between them.
            out.append(("" if out and out[-1].endswith("\n") else "\n") + line + "\n")
        else:
            self._out().append("\n" + "\n".join(c for c in cells if c) + "\n")

    def _close_table(self) -> None:
        rows, cells = self._tables.pop()
        while len(self._rows) > rows:
            self._close_row()
        while len(self._cells) > cells:
            self._close_cell()

    def close(self) -> None:
        super().close()
        while self._tables:
            self._close_table()
        while self._rows:
            self._close_row()
        while self._cells:
            self._close_cell()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._hidden_tag:
            self._hidden_depth += tag == self._hidden_tag
            return
        if tag not in _VOID_TAGS and _is_hidden(tag, attrs, self._rules):
            self._hidden_tag, self._hidden_depth = tag, 1
            return
        if not self._skip_depth and _is_quote_marker(tag, attrs):
            self._out().append(f"\n{QUOTE_MARKER}\n")
        if tag == "table":
            self._tables.append((len(self._rows), len(self._cells)))
        elif tag == "tr":
            if self._rows and (not self._tables or len(self._rows) > self._tables[-1][0]):
                self._close_row()  # the previous <tr> of this table was not closed
            self._rows.append(([], len(self._cells)))
            return
        elif tag in _CELL_TAGS:
            if self._rows and len(self._cells) > self._rows[-1][1]:
                self._close_cell()  # the previous <td> was not closed
            self._cells.append([])
            return
        if tag in _BLOCK_TAGS:
            self._out().append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._hidden_tag:
            if tag == self._hidden_tag:
                self._hidden_depth -= 1
                if not self._hidden_depth:
                    self._hidden_tag = None
        elif tag in _CELL_TAGS:
            if self._cells and (not self._rows or len(self._cells) > self._rows[-1][1]):
                self._close_cell()
        elif tag == "tr":
            if self._rows and (not self._tables or len(self._rows) > self._tables[-1][0]):
                self._close_row()
        elif tag == "table":
            if self._tables:
                self._close_table()
            self._out().append("\n")
        elif tag in _BLOCK_TAGS:
            self._out().append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS and not self._hidden_tag:
            self._out().append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._hidden_tag:
            self.hidden_chars += len(data.strip())
            return
        self._out().append(data)

    def text(self) -> str:
        return "".join(self._parts)


def looks_like_html(body: str) -> bool:
    return bool(re.search(r"<(html|body|div|p|br|span|table)[\s/>]", body, re.IGNORECASE))


def html_to_text(body: str) -> tuple[str, int, list[str]]:
    """Visible text of an HTML body, how many characters of hidden text were dropped, and notes.

    A body hidden as a whole fails closed: no text, and a note that it needs human review (since
    0.10.2; before, the hidden text was passed on)."""
    styles = _StyleCollector()
    styles.feed(body)
    styles.close()
    rules, unresolved = hiding_rules("\n".join(styles.css)) if styles.css else ([], 0)
    parser = _TextExtractor(rules)
    parser.feed(body)
    parser.close()
    text = normalise_text(parser.text())
    notes: list[str] = []
    if unresolved:
        notes.append(f"style sheet hides content by {unresolved} selector(s) not applied here: "
                     "review the email as rendered")
    if not text.replace(QUOTE_MARKER, "").strip() and parser.hidden_chars:
        notes.append("the whole HTML body is hidden: no text is passed on; review the email as rendered")
        return "", parser.hidden_chars, notes
    return text, parser.hidden_chars, notes


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove characters that change what an LLM reads without changing what a person sees."""
    cleaned, count = _INVISIBLE.subn("", text)
    return cleaned, count


def normalise_text(text: str) -> str:
    """Unify whitespace: no-break spaces become spaces, each line is trimmed, blank runs collapse."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()

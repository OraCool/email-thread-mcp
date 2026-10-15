"""HTML email body -> plain text that keeps the line structure.

Line structure matters here: quote headers (`From:` / `Sent:` lines), greetings and sign-off blocks
are all detected line by line, so block elements and `<br>` become line breaks rather than spaces.

Text a human reader cannot see (hidden by CSS, zero-width or bidirectional control characters) is
dropped: it is the usual way to put instructions in an email for the LLM but not for the reviewer.
"""

from __future__ import annotations

import re
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
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all"
    r"|(?<![\w-])font-size\s*:\s*0*\.?0+(px|pt|em|rem|%)?\s*(;|!|$)|(?<![\w-])opacity\s*:\s*0*\.?0+\s*(;|!|$)",
    re.IGNORECASE,
)
# Zero-width, bidirectional-control and other invisible format characters, plus C0 controls but tab/newline.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\x01-\x08\x0b\x0c\x0e-\x1f\x7f]")

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


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    return any(k == "hidden" or (k == "style" and v and _HIDDEN_STYLE.search(v)) for k, v in attrs)


class _TextExtractor(HTMLParser):
    def __init__(self, drop_hidden: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._drop_hidden = drop_hidden
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
        if self._drop_hidden and tag not in _VOID_TAGS and _is_hidden(attrs):
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


def html_to_text(body: str) -> tuple[str, int]:
    """Visible text of an HTML body, and how many characters of hidden text were dropped.

    If the whole body is hidden (broken markup rather than an attack), the hidden text is kept: losing
    the message would be worse, and the caller still sees the count."""
    parser = _TextExtractor()
    parser.feed(body)
    parser.close()
    text = normalise_text(parser.text())
    if not text.replace(QUOTE_MARKER, "").strip() and parser.hidden_chars:
        everything = _TextExtractor(drop_hidden=False)
        everything.feed(body)
        everything.close()
        return normalise_text(everything.text()), parser.hidden_chars
    return text, parser.hidden_chars


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

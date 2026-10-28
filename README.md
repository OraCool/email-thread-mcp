# email-thread-mcp

MCP server (stdio) with six deterministic tools for an accounts-receivable (AR) email agent:
no LLM inside, no network access, no credentials.

| Tool | Role in the agent | Output contract |
|---|---|---|
| `validate_request(request, schema?, id_pointer?)` | very first step: checks the incoming request before any model runs. Parses it as JSON (a text that is not JSON, such as one indented with non-breaking spaces from a rich-text copy, is reported with the reason) and checks it against the caller's JSON Schema. When it fails, `error` is a ready envelope `{error: true, error_code: "INVALID_REQUEST", message, details: [{field, problem}], email_id}` that names the request's own fields, for the workflow to return as its answer. Reports a bad request, never raises (since 0.8.0) | [`contracts/request.schema.json`](contracts/request.schema.json) |
| `parse_email_thread(body, ar_mailboxes?)` | first step: request preprocessing. Splits the raw email body (Outlook/Gmail/Apple HTML or plain text) into the latest message, its signer and greeting, and the quoted history (oldest first). A table row of short cells becomes one line, cells joined by ` \| `, so an invoice stays next to its date and amount | [`contracts/thread.schema.json`](contracts/thread.schema.json) |
| `validate_output(output, categories, thread?, expected_language?, expected_draft_language?)` | last step: deterministic checks of the final envelope. Categories are matched by name, or by integer id when the request's categories carry an `id` and no `name` (since 0.6.0). Corrects only unambiguous problems (category case/punctuation, an id written as a digit string, duplicates, invoice-number noise, human-review flag, markup or a repeated reply prefix in the draft subject); reports the rest, an empty draft as an error (except when the only suggested action is `No AR Action`: an email that needs nothing gets no draft), and with `expected_language` a `language` that does not name it, and a `draft_response.language` that does not name `expected_draft_language` (since 0.9.0; defaults to `expected_language`) | [`contracts/validation.schema.json`](contracts/validation.schema.json) |
| `assemble_output(envelope, schema?, allowed_categories?, thread?, allowed_actions?, expected_language?, expected_draft_language?)` | last step of a workflow whose engine assembles the envelope from earlier steps' values with a template, so no model copies them: the `validate_output` checks, then a JSON Schema check. Returns the envelope plus `validation`; when an error remains, `validation.error` is a ready `PROCESSING_ERROR` envelope (same shape as `validate_request`'s) for the workflow to return instead of the answer (since 0.8.0) | [`contracts/assemble.schema.json`](contracts/assemble.schema.json) |
| `check_language(texts, expected)` | after a step that writes free text in a requested language: gives each text a verdict, `ok`, `drift` (confidently another language) or `undecided` (mixed text, a close variant, or a language the detector does not know), so a workflow can retry the step on drift and ask an LLM about the undecided ones. 142 languages via py3langid, deterministic, no model calls. A word in a script the requested language does not use (an Arabic word in an English text) is a drift even when the rest is right; Latin is allowed everywhere (since 0.9.0). Texts under four words are not checked | [`contracts/language.schema.json`](contracts/language.schema.json) |
| `detect_language(text, fallback?)` | after `parse_email_thread`, on `latest_message.text`: names the incoming email's language (ISO 639-1), so the draft can be written in it. `undecided` (language `null`) for a reply under four words ("Thanks, noted"), a detector under 90% sure, or a language without an ISO 639-1 code. `resolved_language` is the detected language, else `fallback` (the request's `language_preference`) when it is an ISO 639-1 code, else `null` (since 0.9.0) | [`contracts/detect_language.schema.json`](contracts/detect_language.schema.json) |

## Use

Any MCP client that starts stdio servers can run it with `uvx`:

```json
{
  "mcpServers": {
    "email-thread-mcp": {
      "command": "uvx",
      "args": ["email-thread-mcp@0.9.0"]
    }
  }
}
```

Pin a version: a client that keeps the server running may otherwise serve a stale cached build.

Each tool returns one JSON object. Structured arguments (`ar_mailboxes`, `categories`, `output`,
`envelope`, `schema`, `allowed_categories`, `allowed_actions`, `thread`) may be passed as values or as text: clients that render arguments through a template
engine first can send JSON or a Python-style repr, and both are accepted.

A typical agent calls `parse_email_thread` first, as preprocessing, and uses `latest_message`,
`reply_to_name` and `thread_context` in later steps; it calls `validate_output` last, on its final
envelope.

Use `assemble_output` instead when a workflow engine can build the envelope itself. A model that copies
identifiers, invoice numbers or a draft into a final JSON answer changes some of them; a template that
renders each value with a JSON filter does not. Its argument names (`envelope`, `schema`,
`allowed_categories`, `thread`, `allowed_actions`) are chosen not to clash with the usual step outputs, since some engines
fill an argument from a context variable of the same name. A failed check does not raise: it is
reported in `validation`, so the caller still gets the output to review. With `allowed_actions` (the
suggested-action vocabulary), an action that differs from a listed one only in case, punctuation or
underscores is corrected; one outside the list is kept as a new action, with a warning, after its
underscores become spaces and every word gets an uppercase first letter (`send_reminder` → `Send Reminder`). An argument that is still an
unrendered template (a variable it uses was not set) raises an error that says so.
With `thread`, a draft greeting that shares no part of a name with `reply_to_greeting` or
`reply_to_name` ("Dear Mr. Smith," when the customer is Jan Berg) has that name replaced by
`reply_to_greeting`, with a corrected warning. A greeting by first name only is kept.

For the reply, two fields save the drafting model from reading the whole thread:

- `reply_to_greeting`: how to address `reply_to_name`. If our earlier message opened "Dear Mr. Berg,",
  it is `Mr. Berg` for `Jan Berg`; otherwise the name as given. No honorific is added that no message states.
- `our_signature`: the signature block of our quoted message, to sign the reply with. Our message is
  the one from `ar_mailboxes` or, without them, the one signed by the person the customer greets
  ("Dear Ms. Weiss" → Clara Weiss's message). Of that person's quoted messages, the fullest block is
  used, since a short follow-up is often signed with the name alone. Phone, email, URL and address lines are dropped; `null`
  when no quoted message is ours. If parsing fails, falling back to the plain-text body before the first quote marker keeps
the agent working.

## Language support

Inbound mail may be in any language, so the parser treats its three kinds of miss differently:

| Miss | Effect | How it is avoided |
|---|---|---|
| quote boundary | old history read as the new message | found by structure in any language: the mail client's HTML quote marker (Outlook `divRplyFwdMsg`, Gmail `gmail_quote`, Apple/Thunderbird `blockquote type="cite"`), or a `label: value` block that starts with an address and holds a date with a time |
| greeting / signer name | `reply_to_name` is null, the draft greets neutrally | per-language words in `src/email_thread_mcp/vocab.py` |
| sent date | `sent_at` is null (thread order comes from position, not dates) | [`dateparser`](https://pypi.org/project/dateparser/), absolute dates only |

`vocab.py` covers EN, DE, FR, NL, IT, ES, PT, PL, CS, SV, DA, NO, FI. Only EN is `verified` against
real threads; the others are built from Outlook's localized reply headers. A language outside the
list still splits, and `parse_info.warnings` names what was not understood, e.g.
`quoted message 1: header labels not recognised: Kimden, Gönderildi, Kime, Konu`. Search traces
for those warnings to see which language to add next.

To add or verify a language: add its entry to `vocab.py` and a case to `tests/test_languages.py`.
Once a real thread in it (anonymised as below) parses correctly, add it as a fixture and set `verified`.

## Untrusted input

The body is written by the customer, and anyone can send one. The parser:

- drops text a reader cannot see (CSS `display:none`, `visibility:hidden`, `font-size:0`, `opacity:0`,
  `mso-hide:all`, the `hidden` attribute) and zero-width, bidirectional and control characters, and
  reports both in `parse_info.warnings`;
- bounds its own cost: the body is cut at 2M raw / 300k text characters, and dates beyond 50 parser
  calls per email stay null.

It does not make the content trustworthy. The quoted history in particular is plain text the customer
can edit, including a fake message "from" the AR team (role `recipient`). Prompts must treat
`latest_message` and `thread_context` as data, never as instructions.

## Release

1. Bump `version` in `pyproject.toml`, then `uv run pytest`.
2. Add the version to `CHANGELOG.md`.
3. Commit, tag `v<version>`, push both. Never move or reuse a tag or a version.
4. Publish a GitHub release for the tag: `.github/workflows/publish.yml` runs the tests and publishes
   to PyPI (Trusted Publishing; the tag must match the version).
5. Check: `uvx email-thread-mcp@<version>` starts and waits on stdin.

## Develop

```
uv sync
uv run pytest            # 14 real Outlook threads, synthetic variants, 13 languages, hostile input
uv run email-thread-mcp # stdio server
```

Real customer threads stay out of this repository. Locally, `tests/fixtures/` and
`tests/test_client_threads.py` (both gitignored) hold anonymised real threads and their expected
results; the suite runs them when present. Replace every real address and name before using a
thread there.

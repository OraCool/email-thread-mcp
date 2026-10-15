# email-thread-mcp

MCP server (stdio) with two deterministic tools for an accounts-receivable (AR) email agent:
no LLM inside, no network access, no credentials.

| Tool | Role in the agent | Output contract |
|---|---|---|
| `parse_email_thread(body, ar_mailboxes?)` | first step: request preprocessing. Splits the raw email body (Outlook/Gmail/Apple HTML or plain text) into the latest message, its signer and greeting, and the quoted history (oldest first) | [`contracts/thread.schema.json`](contracts/thread.schema.json) |
| `validate_output(output, categories, thread?)` | last step: deterministic checks of the final envelope. Corrects only unambiguous problems (category case/punctuation, duplicates, invoice-number noise, human-review flag); reports the rest | [`contracts/validation.schema.json`](contracts/validation.schema.json) |

## Use

Any MCP client that starts stdio servers can run it with `uvx`:

```json
{
  "mcpServers": {
    "email-thread-mcp": {
      "command": "uvx",
      "args": ["email-thread-mcp@0.3.0"]
    }
  }
}
```

Pin a version: a client that keeps the server running may otherwise serve a stale cached build.

Each tool returns one JSON object. Structured arguments (`ar_mailboxes`, `categories`, `output`,
`thread`) may be passed as values or as text: clients that render arguments through a template
engine first can send JSON or a Python-style repr, and both are accepted.

A typical agent calls `parse_email_thread` first, as preprocessing, and uses `latest_message`,
`reply_to_name` and `thread_context` in later steps; it calls `validate_output` last, on its final
envelope.

For the reply, two fields save the drafting model from reading the whole thread:

- `reply_to_greeting`: how to address `reply_to_name`. If our earlier message opened "Dear Mr. Berg,",
  it is `Mr. Berg` for `Jan Berg`; otherwise the name as given. No honorific is added that no message states.
- `our_signature`: the signature block of our quoted message, to sign the reply with. Our message is
  the one from `ar_mailboxes` or, without them, the one signed by the person the customer greets
  ("Dear Ms. Weiss" → Clara Weiss's message). Phone, email, URL and address lines are dropped; `null`
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
2. Commit, tag `v<version>`, push both. Never move or reuse a tag or a version.
3. `uv build`, check that the sdist holds only `src/`, `README.md` and `pyproject.toml`, then
   `uv publish`.
4. Check: `uvx email-thread-mcp@<version>` starts and waits on stdin.

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

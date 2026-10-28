# Changelog

## 0.9.0

The draft reply is written in the incoming email's language; every other free text stays in the
requested `language_preference`.

- New tool `detect_language(text, fallback?)`: names the language of one text, typically
  `parse_email_thread`'s `latest_message.text`. Returns `{schema_version, language, status, ranking,
  resolved_language}`; `status` is `detected` (ISO 639-1 code) or `undecided` (`language` null: under
  four words, under 90% confidence, or a language without an ISO 639-1 code).
  Dialect labels count towards their language (`ary`, `arz` -> `ar`). With the optional `fallback`
  (the request's language preference), `resolved_language` is the detected language, else the
  fallback when it is an ISO 639-1 code, else null: a key of its own, so a workflow engine that merges
  results into one context keeps it when a later step writes `language`. Contract:
  `contracts/detect_language.schema.json`.
- `validate_output` and `assemble_output`: optional `expected_draft_language`, checked against
  `draft_response.language`; `expected_language` is then checked against `language` only. Without it
  the draft follows `expected_language`, as in 0.8.
- `check_language`: a text with a word in a script the expected language does not use (an Arabic or
  Devanagari word in an English or French text) is a drift, even when the detector is sure of the rest.
  Latin is allowed in every language, and texts under four words are still not checked. Entries gain
  `foreign_script` (`"Arabic"`, or null), and `detected` may be null for a drift found by script alone.
  Contract `language.schema.json` is now schema_version 1.1.

## 0.8.0

- New tool `validate_request`: checks the incoming request against a JSON Schema and returns an
  `INVALID_REQUEST` error envelope naming the request's own fields.
- `assemble_output`: `validation.error` is a `PROCESSING_ERROR` envelope when an error remains
  (assemble contract schema_version 1.1).
- JSON Schema `$ref`s resolve only inside the schema, never by URL or file.

## 0.7.1

- Refuse an unrendered or empty email body and an unrendered language code.

## 0.7.0

- New tool `check_language`: `ok`, `drift` or `undecided` per text, 142 languages via py3langid.

## 0.6.1

- No draft is needed when the only suggested action is `No AR Action`.

## 0.6.0

- Categories are matched by integer id when the request's categories carry an `id` and no `name`.
- Optional `expected_language` for `validate_output` and `assemble_output`.

## 0.5.0 – 0.5.4

- `assemble_output` corrects suggested actions against an `allowed_actions` vocabulary, and new
  actions get the vocabulary's form.
- A draft greeting that names someone other than the person to reply to is replaced.
- An empty draft is an error; markup is cleaned from the draft subject.
- A table row of short cells becomes one line.

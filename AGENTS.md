# AGENTS.md

`mailtester-cli` is a small command-line client for the [MailTester](https://app.mailtester.com) REST API. It lets you verify email addresses, find addresses, and check lists and credits from the terminal.

## Stack

- Python 3.11+, managed with `uv` (`uv sync`, `uv run ...`)
- CLI entry point: `mailtester` (`mailtester_cli.cli:app`). `python -m mailtester_cli` also works.
- Lint/format: `ruff`. Tests: `pytest`, with `respx` for mocking HTTP.
- Keep dependencies minimal: `httpx`, `typer`, and `python-dotenv`.

## Layout

```
src/mailtester_cli/
  cli.py        # Typer app and command definitions
  __init__.py   # __version__ (read from package metadata)
  __main__.py   # python -m support
tests/          # pytest suite, never hits the live API
```

Planned modules are `client.py` (`MailTester`), `models.py` (response types), and `errors.py` (`MailTesterError` hierarchy).

Planned work is tracked in `.specs/BOARD.md`, with one file per SPEC/FEAT/BUG in `.specs/`. The board explains the naming and status conventions.

`README.md` defines the target CLI and Python interface. Build toward it, and update the README in the same change if the interface has to differ.

## Configuration

- The API key comes from the `MAILTESTER_API_KEY` environment variable and is loaded from `.env` when present.
- `.env` is gitignored and holds the real key. `.env.example` holds a placeholder. Never commit, print, or log the real key.
- If the key is missing, exit non-zero with a clear message that tells the user to set `MAILTESTER_API_KEY`.

## MailTester API

- Base URL: `https://app.mailtester.com/api/v1`
- Auth: `Authorization: Bearer evp_...` on every request.
- Responses are JSON. Success: `{ "success": true, "data": ... }`. Error: `{ "success": false, "error": ... }`. Validation errors (422) use `errors` (a field-to-messages map) instead of `error`.
- Timestamps are ISO-8601 UTC.

### Endpoints in scope

| Command (proposed) | Method + path | Scope | Cost |
|---|---|---|---|
| `verify <email>` | `POST /verify` with `email`, plus optional `timeout` (5–60s, default 30) and `smtp_check` (default true) | `verify:single` | 1 credit |
| `batch submit <file>` | `POST /verify/batch` with `emails[]` (1–10,000), plus optional `name`, `callback_url`, `auto_start` (default true). Returns `data.id`. | `verify:batch` | 1 credit per email |
| `batch status <id>` | `GET /verify/batch/{id}`. Returns `status`, `progress` (0–100), `total_emails`, `valid_emails`, `invalid_emails`. | `lists:read` (unconfirmed) | free |
| `batch results <id>` | `GET /verify/batch/{id}/results` with query params `format` (e.g. `json`) and `filter` (e.g. `all`) | `lists:read` (unconfirmed) | free |
| `find <first> <last> <domain>` | `POST /finder/email` with `first_name`, `last_name`, `domain` | `finder` | 3 credits |
| `lists` | `GET /lists` with optional `per_page` (1–100, default 25) | `lists:read` | free |
| `credits` | `GET /credits` | `credits:read` | free |

Batch flow: submit, keep the returned `id`, poll `batch status` until `status` is `completed`, then fetch `batch results`. Poll gently (every few seconds, with backoff) to stay under the rate limit. A `--wait` flag on `batch submit` can run the whole flow in one command. The docs show only `format=json` and `filter=all`, so check the other allowed values before you add choices for them. The API offers more endpoints (webhooks, deliverability, analytics). Add them only when asked, and check the exact request and response shapes against the docs instead of guessing.

### Verdicts

| result | score | meaning |
|---|---|---|
| `valid` | 100 | Mailbox exists and accepts mail |
| `risky` | 50 | Catch-all or low confidence |
| `invalid` | 0 | Mailbox or domain can't receive mail |
| `unknown` | 0 | Undetermined (greylisting or timeout). Not charged. |

Sub-attributes: `disposable`, `role`, `free`, `accept_all`, `spamtrap` (booleans), plus `smtp_provider`, `mx_records`, and `did_you_mean`.

### Errors and rate limits

- `401` bad key · `402` insufficient credits (`available_credits`) · `404` not found · `409` conflict · `422` validation · `429` rate limited (`retry_after`) · `500` engine error · `502` upstream unavailable.
- The default rate limit is 60 requests/min per key. Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`.
- On a `429`, respect `retry_after` and don't hammer the API.

## CLI conventions

- Human-readable output by default. A `--json` flag prints the raw `data` payload for piping to `jq`.
- Send errors to stderr and exit non-zero. Print the API's error message instead of a stack trace.
- Commands that spend credits should say what they will cost. For large batches, ask for confirmation unless `--yes` is passed.
- Keep the HTTP client in one module, separate from command definitions, so commands stay thin.

## Development

- Tests must not hit the live API or spend credits. Mock HTTP responses (for example with `respx` or `pytest-httpx`).
- Make live calls only when the user asks. Prefer the free endpoints (`credits`, `lists`) for smoke tests.
- Run `uv run ruff check . && uv run ruff format . && uv run pytest` before you call work done.

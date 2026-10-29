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
  cli.py        # Typer app, global options, command registration
  commands/
    __init__.py # empty
    _shared.py  # State, client(), handle_errors, print_json, fmt_int, fail
    verify.py   # verify, find
    account.py  # lists, credits
    batch.py    # batch submit/status/results
  client.py     # MailTester: key loading, httpx, envelope, error mapping, rate limits
  errors.py     # MailTesterError hierarchy
  models.py     # response types: VerifyResult, BatchJob, FinderResult, Credits, ListJob, ...
  __init__.py   # __version__ and the MailTester export
  __main__.py   # python -m support
tests/          # pytest suite, never hits the live API
  conftest.py   # load_fixture / load_request / serve / sent_json helpers and the mt fixture
  test_cli_verify_find.py  # verify, find, global --json, error mapping
  test_cli_account.py      # lists, credits
  test_cli_batch.py        # batch submit/status/results, --wait with a fake clock
  fixtures/api/ # redacted live responses, plus synthetic 402/429/500/502
scripts/
  discover.py   # recaptures the fixtures (paid calls need --spend)
```

Planned work is tracked in `.specs/BOARD.md`, with one file per SPEC/FEAT/BUG in `.specs/`. The board explains the naming and status conventions.

`README.md` defines the target CLI and Python interface. Build toward it, and update the README in the same change if the interface has to differ.

## Configuration

- The API key comes from the `MAILTESTER_API_KEY` environment variable and is loaded from `.env` when present.
- `.env` is gitignored and holds the real key. `.env.example` holds a placeholder. Never commit, print, or log the real key.
- If the key is missing, exit non-zero with a clear message that tells the user to set `MAILTESTER_API_KEY`.

## MailTester API

- Base URL: `https://app.mailtester.com/api/v1`
- Auth: `Authorization: Bearer evp_...` on every request.
- Responses are JSON. Success: `{ "success": true, "data": ... }`, with status `200` (`201` for `POST /verify/batch`). Paginated responses add a top-level `meta: {current_page, last_page, per_page, total}`. Error: `{ "success": false, "error": "<message string>" }`. Validation errors (422) use `errors` (a field-to-messages map, which also covers query params) instead of `error`.
- Timestamps are ISO-8601 UTC.
- Real, redacted responses live in `tests/fixtures/api/`, captured by `scripts/discover.py`. Findings and remaining unknowns are in `.specs/002-FEAT-API-DISCOVERY.md`. Check the fixtures before assuming a field exists.

### Endpoints in scope

| Command | Method + path | Scope | Cost |
|---|---|---|---|
| `verify <email>` | `POST /verify` with `email`, plus optional `timeout` (5–60s, default 30) and `smtp_check` (default true) | `verify:single` | 1 credit |
| `batch submit <file>` | `POST /verify/batch` with `emails[]` (1–10,000), plus optional `name`, `callback_url`, `auto_start` (default true). Returns `201` with `data: {id, status, total_emails, created_at}`. | `verify:batch` | 1 credit per email |
| `batch status <id>` | `GET /verify/batch/{id}`. Returns `status`, `progress` (0–100, can be fractional, e.g. `12.5`), `total_emails`, `processed_emails`, `valid_emails`, `invalid_emails`, `unknown_emails`, `completed_at`. There's no risky count. | `verify:batch` | free |
| `batch results <id>` | `GET /verify/batch/{id}/results` with `format` (`json` or `csv`) and `filter` (`all`, `valid`, `invalid`, `risky`, `unknown`). CSV has only the `email,result,reason` columns. Not paginated. Returns `400` (`"Bulk verification is not complete yet"`) until the batch is `completed`. | `verify:batch` | free |
| `find <first> <last> <domain>` | `POST /finder/email` with `first_name`, `last_name`, `domain`. Returns `email`, `status`, `confidence`, `pattern`, `candidates[]`, `credits_used`. A domain with no MX records returns `422` with a plain `error` string and isn't charged. On a catch-all domain, a name that doesn't exist still returns 15 `risky` candidates at `confidence: 40` and costs 3 credits. | `finder` | 3 credits |
| `lists` | `GET /lists` with optional `per_page` (1–100, default 25) and `page`. Items are batches (same `id`), with counts nested in `results: {valid, invalid, unknown, catch_all, risky, ...}`. The sub-counts (`role`, `disposable`, ...) are unreliable. | `lists:read` | free |
| `credits` | `GET /credits` | `credits:read` | free |

Batch flow: submit, keep the returned `id`, poll `batch status` until `status` is `completed`, then fetch `batch results`. Statuses seen so far are `pending` (only with `auto_start: false`; such a batch never starts on its own), `processing`, and `completed`. A 3-email batch took about 30s and an 8-email batch 42s. Poll gently (every few seconds, with backoff) to stay under the rate limit. A `--wait` flag on `batch submit` can run the whole flow in one command. Invalid `format`/`filter` values return `422`. `GET /lists/{id}` and `DELETE /lists/{id}` exist (delete returns `{"success": true, "message": "List deleted"}` with no `data`), but they're out of scope. The API offers more endpoints (webhooks, deliverability, analytics). Add them only when asked, and check the exact request and response shapes against the docs instead of guessing.

### Verdicts

| result | score | meaning |
|---|---|---|
| `valid` | 100 | Mailbox exists and accepts mail |
| `risky` | 50 | Catch-all or low confidence |
| `invalid` | 0 | Mailbox or domain can't receive mail |
| `unknown` | 0 | Undetermined (greylisting or timeout). Not charged. |

Sub-attributes: `disposable`, `role`, `free`, `accept_all`, `spamtrap` (booleans), plus `smtp_provider`, `mx_records`, and `did_you_mean`. `reason` is an uppercase code. Seen in single verify: `CATCH_ALL`, `INVALID`, `NO_MX_RECORD`. Seen in batch rows: `VALID`, `CATCHALL`, `ROLEACCOUNT`, `INVALID`, `NOMXRECORD`, `DISPOSABLE`.

The verify example on the API docs page (`"result": "deliverable"`, `"score": 0.95`) is wrong. The live API matches the table above. (The web app's CSV export does use `undeliverable`, with scores like `5.50`, so the docs example probably came from the engine's own vocabulary.)

Batch result rows are not the same as single verify results. They come from a different engine and can disagree on the same address, on the same day (one address was `valid` in a batch and `risky CATCH_ALL` in single verify; another was `risky CATCHALL` in a batch and `invalid` in single verify). Other differences:
- `score` is on a different scale: 0–10 in a batch (the engine's `5.50` arrives as `5`) and 0–100 in single verify.
- The reason codes drop the underscore: `CATCHALL` in a batch vs `CATCH_ALL` in single verify.
- `smtp_provider` is a label like `"Other"` or `"Gmail"`, where single verify returned `null`.
- Rows have no `mx_records`, `spamtrap`, or `did_you_mean`.

Show the raw values, and don't try to make the two match. Disposable addresses come back as `invalid` with `reason: DISPOSABLE`. The finder's `status` uses the same verdict values, with `confidence` (0–100) instead of `score`. No live `valid` single-verify result has been captured yet: single verify reported Gmail mailboxes as `invalid` and a known-good role address as `risky CATCH_ALL`. `smtp_check: false` returned the same response as the default and was still charged.

### Errors and rate limits

- `400` batch results requested before completion · `401` bad key · `402` insufficient credits (`available_credits`) · `404` not found · `409` conflict · `422` validation · `429` rate limited (`retry_after`) · `500` engine error · `502` upstream unavailable.
- `404` messages depend on the resource, for example `"Bulk verification not found"` for a batch. Show the message from the API instead of hardcoding one.
- `422` usually carries an `errors` map keyed by field (`email`, `timeout`, `emails.0`, `emails.1`, ...), but the finder's no-MX case carries a plain `error` string instead. Handle both.
- Only `401`, `404`, `422`, and `400` have been captured live. The `402`, `429`, `500`, and `502` fixtures are built from the docs, and the units of `retry_after` are assumed to be seconds.
- The default rate limit is 60 requests/min per key, but a key's window can be per minute, hour, or day. Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`. `X-RateLimit-Reset` is a Unix timestamp, and it's missing on `401` responses.
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

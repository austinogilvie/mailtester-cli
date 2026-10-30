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
    _shared.py  # State, get_state/set_state, client(), handle_errors, fail, report_error, error_object, confirm, print_json, fmt_int
    _input.py   # read_addresses (args + file/stdin, dedupe), confirm_spend, CONFIRM_THRESHOLD
    verify.py   # verify (one address or many)
    finder.py   # find, find-domain, find-company
    account.py  # credits
    lists.py    # the lists group: the table, lists show, lists delete
    batch.py    # batch submit/upload/status/wait/results
  client.py     # MailTester: key loading, httpx, retries, envelope, error mapping, rate limits
  errors.py     # MailTesterError hierarchy
  models.py     # response types: VerifyResult, BatchJob, FinderResult, RoleFinderResult, Credits, ListJob, ListPage, ...
  __init__.py   # __version__ and the MailTester export
  __main__.py   # python -m support
tests/          # pytest suite, never hits the live API
  conftest.py   # load_fixture / load_request / serve / sent_json / sent_form helpers, the mt and clock fixtures, and a guard that fails any test that sleeps for real
  test_cli_verify_find.py  # verify, find, global --json, error mapping
  test_cli_finder.py       # find-domain, find-company
  test_cli_account.py      # credits
  test_cli_lists.py        # lists, --page/--per-page/--all, lists show, lists delete
  test_cli_batch.py        # batch submit/upload/status/wait/results, --wait and wait with a fake clock
  fixtures/api/ # redacted live responses, plus synthetic 402/429/500/502 and the verify 422
scripts/
  discover.py   # recaptures the fixtures (paid calls need --spend)
```

Planned work is tracked as GitHub Issues on `austinogilvie/mailtester-cli`, viewed on the "mailtester-cli Roadmap" project board. `.claude/skills/issues/SKILL.md` has the conventions (kind labels, Status, Priority, `Closes #n` in PR bodies) and `.claude/skills/issues/scripts/issue.sh` files, links, boards, and audits issues; hooks in `.claude/hooks/` enforce them. The repo and its issues are public: never put a real address, domain, company, person, key, or balance tied to an account into an issue, PR, comment, or release note; use the fixtures' redacted vocabulary (`example.com`, `*.invalid`, `*.example`). A guard hook blocks the mechanical cases.

`README.md` defines the target CLI and Python interface. Build toward it, and update the README in the same change if the interface has to differ.

## Configuration

- The API key comes from the global `--api-key` option, then the `MAILTESTER_API_KEY` environment variable, then `.env`. An empty value falls through. The `--api-key` value is never printed (`State.api_key` is `repr=False`).
- `MAILTESTER_BASE_URL` (environment, then `.env`) overrides the base URL for a proxy or mock; a `base_url` argument to `MailTester` wins over both. The `.env` value counts only when the key also came from `.env`, so a `.env` can redirect only the key it supplies; when the key comes from elsewhere, `.env` isn't read at all. Tests clear it in the autouse `_isolate_env` fixture.
- `.env` is gitignored and holds the real key. `.env.example` holds a placeholder. Never commit, print, or log the real key.
- If the key is missing, exit non-zero with a clear message that tells the user to set `MAILTESTER_API_KEY`.

## MailTester API

- Base URL: `https://app.mailtester.com/api/v1`
- Auth: `Authorization: Bearer evp_...` on every request.
- Responses are JSON. Success: `{ "success": true, "data": ... }`, with status `200` (`201` for `POST /verify/batch`). Paginated responses add a top-level `meta: {current_page, last_page, per_page, total}`. Error: `{ "success": false, "error": "<message string>" }`. Validation errors (422) use `errors` (a field-to-messages map, which also covers query params) instead of `error`.
- Timestamps are ISO-8601 UTC.
- Real, redacted responses live in `tests/fixtures/api/`, captured by `scripts/discover.py`. Findings and remaining unknowns are in issues #10, #14, and #17 (the three discovery runs). Check the fixtures before assuming a field exists.

### Endpoints in scope

| Command | Method + path | Scope | Cost |
|---|---|---|---|
| `verify <email>...` / `--file` | `POST /verify` once per address, in order, with `email`, plus optional `timeout` (5–60s, default 30) and `smtp_check` (default true) | `verify:single` | 1 credit |
| `batch submit <file>` | `POST /verify/batch` with `emails[]` (1–10,000), plus optional `name`, `callback_url`, `auto_start` (default true). Returns `201` with `data: {id, status, total_emails, created_at}`. | `verify:batch` | 1 credit per email |
| `batch upload <file>` | `POST /verify/batch/upload`, multipart with the file in a part named `file` (CSV, TXT, or XLSX, max 10 MB), plus optional form fields `name`, `callback_url`, `auto_start` (`"1"`/`"0"`). Returns `data: {id, status, total_emails, created_at}` like `batch submit`. Returns `201`. `total_emails` is set in the response. A header row is detected, the email column is found by header or content rather than position, and `.xlsx` is parsed. | `verify:batch` | 1 credit per email |
| `batch status <id>` | `GET /verify/batch/{id}`. Returns `status`, `progress` (0–100, can be fractional, e.g. `12.5`), `total_emails`, `processed_emails`, `valid_emails`, `invalid_emails`, `unknown_emails`, `completed_at`. There's no risky count. | `verify:batch` | free |
| `batch results <id>` | `GET /verify/batch/{id}/results` with `format` (`json` or `csv`) and `filter` (`all`, `valid`, `invalid`, `risky`, `unknown`). CSV has only the `email,result,reason` columns. Not paginated. Returns `400` (`"Bulk verification is not complete yet"`) until the batch is `completed`. | `verify:batch` | free |
| `find <first> <last> <domain>` | `POST /finder/email` with `first_name`, `last_name`, `domain`. Returns `email`, `status`, `confidence`, `pattern`, `candidates[]`, `credits_used`. A domain with no MX records returns `422` with a plain `error` string and isn't charged. On a catch-all domain, a name that doesn't exist still returns 15 `risky` candidates at `confidence: 40` and costs 3 credits. | `finder` | 3 credits |
| `find-domain <domain>` | `POST /finder/domain` with `domain`. Returns `data: {domain, emails[], total, limit, offset, credits_used}`; each `emails[]` item has the same keys as a `find` candidate (`email`, `status`, `confidence`, `is_role`, `is_catch_all`, `reason`). `limit` is 10 and `total` can be larger (a catch-all domain returned 10 of 20); sending `offset` is untested. A no-MX domain is the same free `422` as `find`. On a catch-all domain every item is `risky` at `confidence: 40` with `CATCH_ALL`; real role mailboxes came back `risky` at `confidence: 55` with `ROLE_ACCOUNT`. | `finder` (unconfirmed) | 3 credits |
| `find-company <name>` | `POST /finder/company` with `company`. Resolves the name to a domain and returns the `find-domain` shape plus `company` (the echoed input) and without `offset`. A name that resolves to nothing still returns `200` with a guessed `domain`, `emails: []`, `total: 0`, and `credits_used: 3`: the docs' "zero charge when nothing resolves" is wrong. Whether it accepts a bare domain is untested. | `finder` (unconfirmed) | 3 credits |
| `lists` | `GET /lists` with optional `per_page` (1–100, default 25) and `page`. Items are batches (same `id`), with counts nested in `results: {valid, invalid, unknown, catch_all, risky, ...}`. The sub-counts (`role`, `disposable`, ...) are unreliable. | `lists:read` | free |
| `lists show <id>` | `GET /lists/{id}`. Returns one item in the `lists` shape (`name`, `status`, `progress`, `results`, `credits_used`, `created_at`, `started_at`, `completed_at`). Captured live on a completed list (`list_show`) and a pending one (`list_show_pending`). | `lists:read` (unconfirmed) | free |
| `lists delete <id>...` | `DELETE /lists/{id}`, once per id. Returns `200 {"success": true, "message": "List deleted"}` with no `data`, after which `batch status` on that id is `404`. The docs mention a `409` for a list that's still processing; its message is unverified. `error_409_list_processing` stays synthetic on purpose, because provoking it needs a processing list, which costs 1 credit. Deleting a list deletes its results. | `lists:write` (unconfirmed) | free |
| `credits` | `GET /credits` | `credits:read` | free |

Batch flow: submit, keep the returned `id`, poll `batch status` until `status` is `completed`, then fetch `batch results`. Statuses seen so far are `pending` (only with `auto_start: false`; such a batch never starts on its own), `processing`, and `completed`. A 3-email batch took about 30s and an 8-email batch 42s. Poll gently (every few seconds, with backoff) to stay under the rate limit. A `--wait` flag on `batch submit` runs the whole flow in one command, and `batch upload` does the same for a file, and `batch wait <id>` resumes waiting on any batch. `wait_for_batch` raises `BatchNotStartedError` as soon as a poll returns `pending`, because such a batch never starts on its own; a `429` while polling goes through the client's retry policy, capped by the remaining deadline. Invalid `format`/`filter` values return `422`. `GET /lists/{id}` and `DELETE /lists/{id}` back `lists show` and `lists delete`; see the table. The API offers more endpoints (webhooks, deliverability, analytics). Add them only when asked, and check the exact request and response shapes against the docs instead of guessing.

### Verdicts

| result | score | meaning |
|---|---|---|
| `valid` | 100 | Mailbox exists and accepts mail |
| `risky` | 50 | Catch-all or low confidence |
| `invalid` | 0 | Mailbox or domain can't receive mail |
| `unknown` | 0 | Undetermined (greylisting or timeout). Not charged. |

Sub-attributes: `disposable`, `role`, `free`, `accept_all`, `spamtrap` (booleans), plus `smtp_provider`, `mx_records`, and `did_you_mean`. `reason` is an uppercase code. Seen in single verify: `CATCH_ALL`, `INVALID`, `NO_MX_RECORD`. Seen in the role finders: `CATCH_ALL`, `ROLE_ACCOUNT` (with the underscore, unlike the batch engine's `ROLEACCOUNT`). Seen in batch rows: `VALID`, `CATCHALL`, `ROLEACCOUNT`, `INVALID`, `NOMXRECORD`, `DISPOSABLE`.

The verify example on the API docs page (`"result": "deliverable"`, `"score": 0.95`) is wrong. The live API matches the table above. (The web app's CSV export does use `undeliverable`, with scores like `5.50`, so the docs example probably came from the engine's own vocabulary.)

Batch result rows are not the same as single verify results. They come from a different engine and can disagree on the same address, on the same day (one address was `valid` in a batch and `risky CATCH_ALL` in single verify; another was `risky CATCHALL` in a batch and `invalid` in single verify). Other differences:
- `score` is on a different scale: 0–10 in a batch (the engine's `5.50` arrives as `5`) and 0–100 in single verify.
- The reason codes drop the underscore: `CATCHALL` in a batch vs `CATCH_ALL` in single verify.
- `smtp_provider` is a label like `"Other"` or `"Gmail"`, where single verify returned `null`.
- Rows have no `mx_records`, `spamtrap`, or `did_you_mean`.

Show the raw values, and don't try to make the two match. Disposable addresses come back as `invalid` with `reason: DISPOSABLE`. The finder's `status` uses the same verdict values, with `confidence` (0–100) instead of `score`. No live `valid` single-verify result has been captured yet: single verify reported Gmail mailboxes as `invalid` and a known-good role address as `risky CATCH_ALL`. `smtp_check: false` returned the same response as the default and was still charged.

### Errors and rate limits

- `400` batch results requested before completion · `401` bad key · `402` insufficient credits (`available_credits`) · `404` not found · `409` conflict · `422` validation · `429` rate limited (`retry_after`) · `500` engine error · `502` upstream unavailable.
- `404` messages depend on the resource, for example `"Bulk verification not found"` for a batch and `"List not found"` for a list. Show the message from the API instead of hardcoding one.
- `422` usually carries an `errors` map keyed by field (`email`, `timeout`, `emails.0`, `emails.1`, ...), but the finder's no-MX case carries a plain `error` string instead. Handle both.
- Only `401`, `404`, `422`, and `400` have been captured live. The `402`, `429`, `500`, and `502` fixtures are built from the docs, `error_422_email` (a malformed address to `POST /verify`) is built from Laravel's wording until `discover.py --verify-email` records it (step 3p), and the units of `retry_after` are assumed to be seconds.
- The default rate limit is 60 requests/min per key, but a key's window can be per minute, hour, or day. Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`. `X-RateLimit-Reset` is a Unix timestamp, and it's missing on `401` responses.
- The client retries a `429` after `retry_after` (then the seconds until `X-RateLimit-Reset`, then a 1, 2, 4... backoff), connection failures, and `5xx` on `GET`/`DELETE`, at most `max_retries` (2) times and `max_retry_wait` (60s) in total, in `_request`. Commands never add retry loops; a `POST` is never replayed after a `5xx` or a read timeout. The CLI prints one stderr line per retry through `on_retry`.

## CLI conventions

- Human-readable output by default. A `--json` flag prints the raw `data` payload for piping to `jq`.
- Send errors to stderr and exit non-zero. Print the API's error message instead of a stack trace. Every error goes through `fail` (or `report_error` for one item of many): `Error: ...` in text mode, one `error_object` JSON line under `--json` (`error`, `type`, `status_code`, then `errors`, `available_credits`, `retry_after`, `hint` when present). Notes (cost, retries, progress, `Aborted.`, `Interrupted.`) stay text in both modes, so under `--json` the last stderr line of a failed command is the error object.
- Exit codes: `0` success, `1` error, `2` usage, `3` a `verify` verdict matched `--fail-on`, `130` interrupted.
- Every command that spends credits says what it will cost on stderr. At 100 addresses or more (`CONFIRM_THRESHOLD`), `verify` and `batch submit` ask for confirmation unless `--yes` is passed.
- Commands that read many addresses (`verify`, `batch submit`) go through `read_addresses`, which drops case-insensitive repeats so a repeat never costs a second credit.
- Keep the HTTP client in one module, separate from command definitions, so commands stay thin.

## Development

- Tests must not hit the live API or spend credits. Mock HTTP responses (for example with `respx` or `pytest-httpx`).
- Make live calls only when the user asks. Prefer the free endpoints (`credits`, `lists`) for smoke tests.
- Run `uv run ruff check . && uv run ruff format . && uv run pytest` before you call work done.

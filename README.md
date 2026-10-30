# mailtester-cli

[![CI](https://github.com/austinogilvie/mailtester-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/austinogilvie/mailtester-cli/actions/workflows/ci.yml)

Verify and find email addresses from your terminal or from Python code with the [MailTester](https://app.mailtester.com) API.

> **Status: v0.3.0.** Adds `verify` for many addresses, automatic retries, JSON errors and `--fail-on` for scripts, `--api-key`, and `MAILTESTER_BASE_URL`; see the [changelog](CHANGELOG.md). The CLI and the Python client below are both built and tested.

## Install

```bash
uv tool install git+https://github.com/austinogilvie/mailtester-cli
# or pin this release:
uv tool install git+https://github.com/austinogilvie/mailtester-cli@v0.3.0
```

Or, for development:

```bash
git clone https://github.com/austinogilvie/mailtester-cli
cd mailtester-cli
uv sync
```

## Configure

Create an API key on the MailTester API page. Then set it in your environment or in a `.env` file:

```bash
cp .env.example .env
# then edit .env:
MAILTESTER_API_KEY=evp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`--api-key` (before the command, like `--json`) wins over the environment variable, which wins over `.env`. `mailtester` looks for `.env` in the directory you run it from (and its parents), so an installed `mailtester` still finds your project's key. `--api-key` shows up in process lists and shell history, so prefer the variable for anything long-lived.

To point the client at a proxy, a staging host, or a local mock, set `MAILTESTER_BASE_URL` (for example `http://localhost:8080/api/v1`) in the environment or `.env`. A `MAILTESTER_BASE_URL` in `.env` counts only when the key comes from the same `.env`, so a `.env` in some other project's directory can't send the key from your environment or `--api-key` to its own host.

## CLI

```text
$ mailtester --help

 Usage: mailtester [OPTIONS] COMMAND [ARGS]...

 Verify and find email addresses with the MailTester API.

╭─ Options ───────────────────────────────────────────────────────────────╮
│ --json                  Print raw JSON instead of formatted output.     │
│ --api-key        <str>  API key for this run. Shows in process lists;   │
│                         prefer MAILTESTER_API_KEY.                      │
│ --version               Show the version and exit.                      │
│ --help                  Show this message and exit.                     │
╰─────────────────────────────────────────────────────────────────────────╯
╭─ Commands ──────────────────────────────────────────────────────────────╮
│ verify        Verify email addresses. (1 credit each)                   │
│ batch         Submit, track, and fetch bulk verification jobs.          │
│ find          Find a person's email from name + domain. (3 credits)     │
│ find-domain   Find role mailboxes at a domain. (3 credits)              │
│ find-company  Find a company's domain and role mailboxes. (3 credits)   │
│ lists         List, inspect, and delete your bulk verification jobs.    │
│ credits       Show your credit balance.                                 │
╰─────────────────────────────────────────────────────────────────────────╯
```

Results go to stdout. Errors, prompts, cost notes, and progress go to stderr, so `> file` captures only results. Exit codes: `0` success, `1` an API or runtime error (including a `verify` run in which any address failed), no match from a finder (`find`, `find-domain`, `find-company`), or a declined prompt, `2` usage error, `3` a `verify` verdict matched `--fail-on`, `130` interrupted. Under `--json`, an error is one JSON object on stderr; see [Scripting](#scripting) for the two exit-`1` cases that aren't errors. A rate-limited or failed request is retried a couple of times before a command gives up, and each retry prints a line on stderr, such as `Rate limited, retrying in 30s...`.

### Verify addresses

```text
$ mailtester verify jane@acme.com
This will use 1 credit.
jane@acme.com  risky  score=50
  reason      CATCH_ALL
  provider    -
  flags       accept_all
```

`verify` costs 1 credit per address and says so on stderr. `--timeout N` sets the server-side SMTP timeout (5–60 seconds). `--no-smtp` asks the API to skip the SMTP check, but the call still costs 1 credit and the API may check anyway.

Use `--json` to get machine-readable output:

```bash
mailtester --json verify jane@acme.com | jq .result
# "risky"
```

Give several addresses, or a file with one per line through `--file` (`-` reads stdin), and `verify` checks them one at a time in order, arguments first. Each address prints one line as its answer arrives: the address, verdict, score, and reason code. Flags, provider, and suggestions are in `--json`, which prints JSON Lines (one object per line, no wrapping array) when there is more than one address after repeats are dropped, and the usual indented object when there is one.

```text
$ mailtester verify jane@acme.com bob@acme.com --file more.txt
Skipped 1 duplicate.
This will use 4 credits.
jane@acme.com      risky    score=50   CATCH_ALL
bob@acme.com       invalid  score=0    INVALID
sales@example.org  risky    score=50   CATCH_ALL
nobody@nomx.test   invalid  score=0    NO_MX_RECORD

$ mailtester --json verify --file leads.txt | jq -c 'select(.result == "valid") | .email'
```

Blank lines are skipped, and repeated addresses are dropped before the cost is counted, ignoring case and keeping the first spelling. For 100 or more addresses `verify` asks before starting, unless you pass `--yes`; reading from stdin needs `--yes` at that size because stdin can't answer the prompt. If the API rejects one address as malformed (`422`), `verify` prints `Error: <address>: <message>` and moves on. Any other error (a bad key, no credits, a rate limit that outlasts the retries, a server or connection failure) would hit every remaining address too, so it stops the run and names the address it stopped at, whose request went out and may have been charged, and how many after it were not sent. The exit code is `1` if any address failed and `0` when every address got a verdict, whatever the verdicts are.

One request at a time takes about a minute per 60 addresses at the default rate limit. For hundreds of addresses, `batch submit` is faster, but it uses a different engine whose results can differ (see below).

### Verify a batch

The input file holds one address per line, and blank lines are skipped. Repeated addresses are dropped before the cost is counted, ignoring case, and `batch submit` says how many it skipped, so a repeat never costs a second credit. `batch submit` prints the cost to stderr. For 100 or more addresses it asks before submitting, unless you pass `--yes`. Use `-` to read from stdin, which needs `--yes` for 100 or more addresses because stdin can't answer the prompt. `--callback-url URL` asks MailTester to call that URL when the batch completes; the CLI only passes it along and doesn't receive webhooks.

```text
$ mailtester batch submit leads.txt --name "Q3 leads"
This will use up to 1,000 credits.
Continue? [y/N]: y
Submitted batch 1287 (1,000 emails)

$ mailtester batch status 1287
Batch 1287  processing  64%  (640/1,000)

$ mailtester batch results 1287 > results.json
$ mailtester batch results 1287 --filter risky --format csv > risky.csv
```

`--filter` accepts `all` (the default), `valid`, `invalid`, `risky`, or `unknown`. `--format` accepts `json` (the default) or `csv`. CSV output has only the `email`, `result`, and `reason` columns. Asking for results before the batch is `completed` is an error, and the message points you back to `batch status`.

Batch results don't always match single `verify` results. The API scores batch rows on a different scale (0–10 instead of 0–100, so the same risky address can score 5 in a batch and 50 in `verify`), spells reason codes without underscores (`CATCHALL` vs `CATCH_ALL`), and can even reach a different verdict for the same address. Batch rows also leave out `mx_records`, `spamtrap`, and `did_you_mean`. The CLI shows what the API returns.

Real lead files are usually CSV or XLSX exports with several columns. `batch upload` sends such a file to MailTester as is (CSV, TXT, or XLSX, up to 10 MB), and the API picks out the addresses. For a text file the cost line counts its non-blank lines, so a header row makes the estimate one too high. For an XLSX file the count isn't known until MailTester has read it, so the command asks before uploading unless you pass `--yes`. `--name`, `--callback-url`, `--yes`, and `--wait` work as they do for `batch submit`. Reading from stdin isn't supported; pipe addresses to `batch submit -` instead.

```text
$ mailtester batch upload leads.csv --name "Q3 leads"
This will use up to 5,401 credits.
Continue? [y/N]: y
Submitted batch 1288 (5,400 emails)

$ mailtester batch upload leads.xlsx --yes --wait > results.json
The address count in leads.xlsx isn't known until MailTester reads it. This will use 1 credit per address.
Submitted batch 1289 (5,400 emails)
Batch 1289  processing  12.5%  (675/5,400)
...
Batch 1289  completed  100%  (5,400/5,400)
```

MailTester skips a header row and finds the email column by its header or content, not its position, so a `name,email` export works as is.

Or submit (or upload) and wait in one step. `--wait` polls until the batch completes, shows progress on stderr, and writes the results to stdout. It takes the same `--filter` and `--format` options as `batch results`. It gives up after `--timeout` seconds (default 1800), but the batch keeps running.

```bash
mailtester batch submit leads.txt --wait --yes > results.json
```

If a wait is interrupted or times out, or you submitted without `--wait`, `batch wait` picks it up: it polls the same way, prints progress to stderr, and writes the results to stdout with the same `--filter`, `--format`, and `--timeout` options. A batch created with `auto_start: false` is `pending` and never starts on its own, so `batch wait` says so and exits instead of polling.

```text
$ mailtester batch wait 1288 --format csv > results.csv
Batch 1288  processing  64%  (3,456/5,400)
Batch 1288  processing  71.2%  (3,845/5,400)
Batch 1288  completed  100%  (5,400/5,400)
```

### Find an address

```text
$ mailtester find Jane Doe acme.com
This will use 3 credits.
jane.doe@acme.com  valid  confidence=95  pattern={first}.{last}
```

The finder tries several address patterns. Use `--json` to see every candidate.

### Find role mailboxes

```text
$ mailtester find-domain acme.com
This will use 3 credits.
info@acme.com     risky  confidence=55
contact@acme.com  risky  confidence=55
support@acme.com  risky  confidence=55
sales@acme.com    risky  confidence=55
team@acme.com     risky  confidence=55

$ mailtester find-company "Acme Inc"
This will use 3 credits.
Resolved Acme Inc to acme.com.
info@acme.com     risky  confidence=55
contact@acme.com  risky  confidence=55
support@acme.com  risky  confidence=55
sales@acme.com    risky  confidence=55
team@acme.com     risky  confidence=55
```

`find-domain` checks the usual role mailboxes (`info@`, `sales@`, `support@`, ...) at a domain; `find-company` resolves a company name to its domain first and prints the domain it chose on stderr. Both cost 3 credits whether or not anything is found. A call returns at most 10 mailboxes; when there are more, `Showing 10 of 20 role mailboxes.` goes to stderr. On a catch-all domain every mailbox comes back `risky` at `confidence=40` and a note on stderr says so. Nothing found means a message on stderr and exit `1`. Use `--json` for `is_catch_all`, `reason`, and `total`.

### Lists and credits

```text
$ mailtester lists
ID    NAME      STATUS     EMAILS  VALID  INVALID  RISKY  CREATED
1287  Q3 leads  completed   1,000    820       90     60  2026-10-01
Page 1 of 3 (57 lists). Use --page 2 or --all for more.

$ mailtester lists --all --json | jq '.[] | select(.status == "pending") | .id'
1492
1493

$ mailtester lists show 1287
List 1287  Q3 leads  completed  100%  (1,000/1,000)
  valid       820
  invalid     90
  risky       60
  unknown     30
  credits     1,000
  created     2026-10-01 09:12:03 UTC
  started     2026-10-01 09:12:04 UTC
  completed   2026-10-01 09:40:51 UTC

$ mailtester lists delete 1492 1493
Delete 2 lists (1492, 1493)? [y/N]: y
Deleted list 1492
Deleted list 1493

$ mailtester credits
Balance    5,000 credits
Account    pay_as_you_go
Rate limit 59 of 60 left, resets 2026-10-30 14:02:11 UTC
```

`lists` shows 25 lists per page. Use `--page` and `--per-page` to move around, or `--all` to fetch every page. The page hint goes to stderr, so `> file` captures only the table. Accounts with a monthly allowance get an extra `credits` line, for example `Monthly    1,000 (renews 2026-11-01)`. The `Rate limit` line comes from the response headers, which don't say how long the window is (per minute by default, but a key's window can be per hour or day); `--json credits` prints only the API's `data`, without it.

`lists delete` takes one or more ids and asks once before deleting unless you pass `--yes`. Deleting a list removes its results. Each deleted id prints `Deleted list <id>`; an id that isn't found or is still processing prints the API's error and the command carries on with the rest, exiting `1` at the end if any id failed. Any other error, such as a rate limit or a bad key, stops the command. The API refuses to delete a list that's still processing, and the error says so.

### Scripting

Under `--json`, an error is one JSON object on one line of stderr instead of an `Error: ...` sentence. Errors never go to stdout, which holds only results. The exit code is the same as in text mode.

```text
$ mailtester --json verify x@example.com
This will use 1 credit.
{"error": "Insufficient credits", "type": "InsufficientCreditsError", "status_code": 402, "available_credits": 0}
```

The keys are `error` (the API's message), `type` (the exception class, or `UsageError`/`CLIError` for a problem the CLI caught itself), and `status_code` (`null` when no response came back), then, only when they apply: `errors` (the field map of a `422`), `available_credits`, `retry_after`, and `hint` (what to run next, such as `Resume with: mailtester batch wait 1288`). `lists delete` prints one object per failed id with an `id` key, and a `verify` run of several addresses prints one per failed address with an `email` key. Notes such as cost lines, retry lines, and batch progress stay text, so the rule for a script is: when a command fails on an error (anything that prints `Error:` in text mode), the last line on stderr is the JSON object.

Two cases exit `1` without one, because they aren't errors. A `find`, `find-domain`, or `find-company` with no match still prints its payload on stdout, and a note on stderr, such as `No address found for Jane Doe at example.com.` Answering no to a prompt prints `Aborted.` on stderr. Errors from the argument parser itself (a missing argument, an out-of-range `--timeout`) are text and exit `2`.

`verify --fail-on VERDICT[,VERDICT]` exits `3` when a verdict is one you name (`valid`, `risky`, `invalid`, `unknown`), after printing the result as usual. With several addresses it exits `3` if any of them matched, unless an address failed, which exits `1`.

```bash
mailtester verify "$EMAIL" --fail-on invalid,unknown && send "$EMAIL"
```

For a second account, pass `--api-key` before the command. For a mock server, set `MAILTESTER_BASE_URL` (see [Configure](#configure)).

## Python

The CLI is a thin layer over a Python client that you can use directly.

```python
from mailtester_cli import MailTester

mt = MailTester()  # reads MAILTESTER_API_KEY (and MAILTESTER_BASE_URL) from the environment or .env
# or: MailTester(api_key="evp_...")
```

### Verify

```python
result = mt.verify("jane@acme.com")

result.email         # "jane@acme.com"
result.result        # "valid" | "risky" | "invalid" | "unknown"
result.score         # 50
result.reason        # "CATCH_ALL"
result.is_valid      # False
result.accept_all    # True
result.disposable    # False
result.did_you_mean  # None
```

### Batch

```python
emails = ["user1@example.com", "user2@example.com", "user3@example.com"]

job = mt.submit_batch(emails, name="Q3 leads")
job.id               # 123
job = mt.upload_batch("leads.csv", name="Q3 leads")  # CSV, TXT, or XLSX, up to 10 MB, sent as is

job = mt.batch_status(job.id)
job.status           # "processing"
job.progress         # 64 (a percentage, can be fractional)

job = mt.wait_for_batch(job.id)          # polls until completed
results = mt.batch_results(job.id)       # list[VerifyResult]
risky = mt.batch_results(job.id, filter="risky")
csv_text = mt.batch_results_csv(job.id)  # str with columns email,result,reason
# wait_for_batch raises BatchNotStartedError if the batch is pending (auto_start=False),
# BatchFailedError on a failure status, and BatchTimeoutError after `timeout` seconds.

valid = [r.email for r in results if r.is_valid]
```

### Find, lists, credits

```python
match = mt.find_email("Jane", "Doe", "acme.com")
match.email          # "jane.doe@acme.com"
match.status         # "valid"
match.confidence     # 95
match.candidates     # list[FinderCandidate], one per pattern tried

roles = mt.find_domain("acme.com")       # RoleFinderResult, 3 credits found or not
roles.emails                             # list[FinderCandidate]: info@, support@, sales@, ...
roles.total                              # 5; at most roles.limit (10) come back per call

company = mt.find_company("Acme Inc")    # resolves the name first; same shape plus .company
company.domain                           # "acme.com"

for job in mt.lists():                   # the first page, 25 lists
    print(job.id, job.name, job.status)

page = mt.lists_page(page=2)             # ListPage: .items plus .current_page, .last_page, .total
for job in mt.iter_lists():              # every page, 100 lists per request
    ...

job = mt.get_list(1287)                  # one ListJob, with per-verdict counts and timestamps
job.results.valid                        # 820
job.completed_at                         # datetime, or None until the list completes

mt.delete_list(1287)                     # "List deleted"; raises ConflictError while processing

mt.credits().balance  # 5000
```

### Errors

The client retries on its own before raising. A `429` is retried after `retry_after` seconds, and a connection failure, or a `5xx` on a read-only call, is retried with a short backoff: at most `max_retries` (2) retries and `max_retry_wait` (60) seconds of waiting per call. A paid `POST` is never replayed after a server error. `MailTester(max_retries=0)` turns retries off, and `on_retry` is called with a `RetryAttempt` before each wait.

Every API error raises a subclass of `MailTesterError`:

```python
from mailtester_cli.errors import (
    MailTesterError,
    AuthenticationError,      # 401
    InsufficientCreditsError, # 402
    RateLimitError,           # 429
    BatchNotStartedError,     # wait_for_batch: the batch is pending and won't start on its own
)

try:
    mt.verify("jane@acme.com")
except InsufficientCreditsError as e:
    print(f"Out of credits ({e.available_credits} left)")
except RateLimitError as e:
    print(f"Still rate limited after retrying; try again in {e.retry_after}s")
except MailTesterError as e:
    print(f"MailTester error: {e}")
```

### Modules

| Module | Contents |
|---|---|
| `mailtester_cli.client` | `MailTester`, the API client, and `RetryAttempt`, what its `on_retry` callback receives. Both also importable from `mailtester_cli`. |
| `mailtester_cli.models` | `VerifyResult`, `FinderResult`, `RoleFinderResult`, `BatchJob`, `ListJob`, `ListPage`, `Credits`, and other response types |
| `mailtester_cli.errors` | `MailTesterError` and its subclasses |
| `mailtester_cli.cli` | The `mailtester` Typer app |

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format . && uv run pytest
```

Tests mock all HTTP calls and never spend credits.

## License

MIT. See [LICENSE](LICENSE).

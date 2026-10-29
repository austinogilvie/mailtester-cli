# mailtester-cli

Verify and find email addresses from your terminal or from Python code with the [MailTester](https://app.mailtester.com) API.

> **Status: pre-release.** This README describes the planned interface. The CLI and Python API below are the targets that implementation works toward.

## Install

```bash
uv tool install git+https://github.com/austinogilvie/mailtester-cli
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

## CLI

```text
$ mailtester --help

 Usage: mailtester [OPTIONS] COMMAND [ARGS]...

 Verify and find email addresses with the MailTester API.

╭─ Options ──────────────────────────────────────────────────────────╮
│ --json         Print raw JSON instead of formatted output.         │
│ --version      Show the version and exit.                          │
│ --help         Show this message and exit.                         │
╰────────────────────────────────────────────────────────────────────╯
╭─ Commands ─────────────────────────────────────────────────────────╮
│ verify    Verify a single email address. (1 credit)                │
│ batch     Submit, track, and fetch bulk verification jobs.         │
│ find      Find a person's email from name + domain. (3 credits)    │
│ lists     List your bulk verification jobs.                        │
│ credits   Show your credit balance.                                │
╰────────────────────────────────────────────────────────────────────╯
```

### Verify one address

```text
$ mailtester verify jane@acme.com
jane@acme.com  risky  score=50
  reason      CATCH_ALL
  provider    -
  flags       accept_all
```

Use `--json` to get machine-readable output:

```bash
mailtester --json verify jane@acme.com | jq .result
# "risky"
```

### Verify a batch

The input file holds one address per line. Use `-` to read from stdin.

```text
$ mailtester batch submit leads.txt --name "Q3 leads"
This will use up to 1,000 credits. Continue? [y/N]: y
Submitted batch 1287 (1,000 emails)

$ mailtester batch status 1287
Batch 1287  processing  64%  (640/1,000)

$ mailtester batch results 1287 > results.json
$ mailtester batch results 1287 --filter risky --format csv > risky.csv
```

`--filter` accepts `all` (the default), `valid`, `invalid`, `risky`, or `unknown`. `--format` accepts `json` (the default) or `csv`. CSV output has only the `email`, `result`, and `reason` columns.

Batch results don't always match single `verify` results. The API scores batch rows on a different scale (the same risky address can score 5 in a batch and 50 in `verify`). Batch rows also leave out `mx_records`, `spamtrap`, and `did_you_mean`. The CLI shows what the API returns.

Or submit and wait in one step:

```bash
mailtester batch submit leads.txt --wait --yes > results.json
```

### Find an address

```text
$ mailtester find Jane Doe acme.com
jane.doe@acme.com  valid  confidence=95  pattern={first}.{last}
```

The finder tries several address patterns. Use `--json` to see every candidate.

### Lists and credits

```text
$ mailtester lists
ID    NAME      STATUS     EMAILS  VALID  INVALID  RISKY  CREATED
1287  Q3 leads  completed   1,000    820       90     60  2026-10-01

$ mailtester credits
Balance    5,000 credits
Account    pay_as_you_go
```

Accounts with a monthly allowance get an extra line, for example `Monthly    1,000 (renews 2026-11-01)`.

## Python

The CLI is a thin layer over a Python client that you can use directly.

```python
from mailtester_cli import MailTester

mt = MailTester()  # reads MAILTESTER_API_KEY from the environment or .env
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

job = mt.batch_status(job.id)
job.status           # "processing"
job.progress         # 64

job = mt.wait_for_batch(job.id)          # polls until completed
results = mt.batch_results(job.id)       # list[VerifyResult]
risky = mt.batch_results(job.id, filter="risky")
csv_text = mt.batch_results_csv(job.id)  # str with columns email,result,reason

valid = [r.email for r in results if r.is_valid]
```

### Find, lists, credits

```python
match = mt.find_email("Jane", "Doe", "acme.com")
match.email          # "jane.doe@acme.com"
match.status         # "valid"
match.confidence     # 95
match.candidates     # list[FinderCandidate], one per pattern tried

for job in mt.lists():
    print(job.id, job.name, job.status)

mt.credits().balance  # 5000
```

### Errors

Every API error raises a subclass of `MailTesterError`:

```python
from mailtester_cli.errors import (
    MailTesterError,
    AuthenticationError,      # 401
    InsufficientCreditsError, # 402
    RateLimitError,           # 429
)

try:
    mt.verify("jane@acme.com")
except InsufficientCreditsError as e:
    print(f"Out of credits ({e.available_credits} left)")
except RateLimitError as e:
    print(f"Slow down, retry in {e.retry_after}s")
except MailTesterError as e:
    print(f"MailTester error: {e}")
```

### Modules

| Module | Contents |
|---|---|
| `mailtester_cli.client` | `MailTester`, the API client. Also importable from `mailtester_cli`. |
| `mailtester_cli.models` | `VerifyResult`, `FinderResult`, `BatchJob`, `ListJob`, `Credits`, and other response types |
| `mailtester_cli.errors` | `MailTesterError` and its subclasses |
| `mailtester_cli.cli` | The `mailtester` Typer app |

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format . && uv run pytest
```

Tests mock all HTTP calls and never spend credits.

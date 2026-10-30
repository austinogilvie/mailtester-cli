# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-30

### Added

- `batch upload FILE` sends a CSV, TXT, or XLSX file to MailTester as is. It takes the same `--name`, `--yes`, and `--wait` options as `batch submit`.
- `batch wait ID` resumes waiting on any batch and prints its results the way `submit --wait` does.
- `--callback-url URL` on `batch submit` and `batch upload` asks MailTester to call that URL when the batch completes.
- `lists --page N`, `--per-page N`, and `--all`, with a stderr hint when there are more pages.
- `lists show ID` prints one list's status and counts.
- `lists delete ID...` deletes lists, asking first unless `--yes` is passed.
- `find-domain DOMAIN` and `find-company NAME` find the role mailboxes (`info@`, `sales@`, ...) at a domain or a company. Each costs 3 credits.
- `verify` prints `This will use 1 credit.` on stderr before the call, like the finders.
- Python client: `upload_batch`, `lists_page`, `iter_lists`, `get_list`, `delete_list`, `find_domain`, `find_company`, and the `ListPage`, `RoleFinderResult`, and `BatchNotStartedError` types.
- An MIT license and project URLs in the package metadata.

### Changed

- Waiting on a batch created with `auto_start: false` stops at once with `BatchNotStartedError`, because such a batch never starts on its own. Before, it polled until the timeout.
- Timeout and Ctrl-C hints during a wait point to `mailtester batch wait <id>`.
- `BatchJob.progress` and `ListJob.progress` are floats, since the API can report fractional progress such as `12.5`.
- `--no-smtp` help says what the API actually does: the call still costs 1 credit, and the API may check anyway.

### Fixed

- `batch results` on a batch that hasn't finished points you to `batch status` instead of only printing the API's `400`.

## [0.1.0] - 2026-10-29

### Added

- The `mailtester` CLI: `verify`, `find`, `batch submit` (with `--wait`), `batch status`, `batch results` (`--filter`, `--format json|csv`), `lists`, and `credits`, plus the global `--json` and `--version` options. Results go to stdout; errors, prompts, cost notes, and progress go to stderr. Paid commands state their cost, and `batch submit` asks before 100 or more addresses unless `--yes` is passed.
- The `MailTester` Python client: API key loading from `MAILTESTER_API_KEY` or `.env`, the response envelope, typed results (`VerifyResult`, `FinderResult`, `BatchJob`, `Credits`, `ListJob`, ...), a `MailTesterError` hierarchy mapped from the API's status codes, rate-limit headers, and `wait_for_batch`.

[0.2.0]: https://github.com/austinogilvie/mailtester-cli/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/austinogilvie/mailtester-cli/releases/tag/v0.1.0

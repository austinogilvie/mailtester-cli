import copy
import json

import httpx
import pytest
from conftest import load_fixture, sent_json, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL, MailTester

SUBMIT = f"{BASE_URL}/verify/batch"
STATUS = f"{BASE_URL}/verify/batch/1457"
RESULTS = f"{BASE_URL}/verify/batch/1457/results"
EMAILS = ["batch1@example.com", "batch2@example.com", "batch3@example.net"]
PROCESSING = "Batch 1457  processing  0%  (0/3)"
COMPLETED = "Batch 1457  completed  100%  (3/3)"

runner = CliRunner()


class FakeClock:
    """Stands in for time.monotonic/time.sleep: sleep records the delay and advances now."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


@pytest.fixture
def guard(respx_mock):
    """A catch-all route: any request at all shows up as guard.called."""
    return respx_mock.route().respond(500)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(MailTester, "_sleep", staticmethod(fake.sleep))
    monkeypatch.setattr(MailTester, "_clock", staticmethod(fake.now))
    return fake


def fixture_data(name: str):
    return copy.deepcopy(load_fixture(name)[2]["data"])


def status_response(name: str, **data) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": fixture_data(name) | data})


def write_emails(tmp_path, lines: list[str]) -> str:
    path = tmp_path / "emails.txt"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def many(n: int) -> list[str]:
    return [f"user{i}@example.com" for i in range(n)]


def serve_results(respx_mock, name: str, format_: str = "json", filter_: str = "all"):
    params = {"format": format_, "filter": filter_}
    return serve(respx_mock, name, RESULTS, params__eq=params)


def csv_text() -> str:
    return load_fixture("batch_results_csv_all")[2]


def json_rows(name: str = "batch_results_json_all") -> list[dict]:
    return fixture_data(name)


# submit


def test_submit_small_file(respx_mock, api_key, tmp_path):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, [EMAILS[0], "", f"  {EMAILS[1]}  ", EMAILS[2]])
    result = runner.invoke(app, ["batch", "submit", path])
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": EMAILS}
    assert result.stderr == "This will use up to 3 credits.\n"
    assert result.stdout == "Submitted batch 1457 (3 emails)\n"


def test_submit_with_name(respx_mock, api_key, tmp_path):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["batch", "submit", path, "--name", "discovery"])
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": EMAILS, "name": "discovery"}


def test_submit_from_stdin(respx_mock, api_key):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    result = runner.invoke(app, ["batch", "submit", "-"], input="\n".join(EMAILS) + "\n")
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": EMAILS}


def test_submit_single_address_says_credit(respx_mock, api_key, tmp_path):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, EMAILS[:1])])
    assert result.stderr == "This will use up to 1 credit.\n"


def test_submit_json(respx_mock, api_key, tmp_path):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    result = runner.invoke(app, ["--json", "batch", "submit", write_emails(tmp_path, EMAILS)])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("batch_submit")


# --callback-url

HOOK = "https://hooks.example.com/mt"


def test_submit_callback_url_is_sent(respx_mock, api_key, tmp_path):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["batch", "submit", path, "--callback-url", HOOK])
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": EMAILS, "callback_url": HOOK}


def test_submit_callback_url_with_name(respx_mock, api_key, tmp_path):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, EMAILS)
    args = ["batch", "submit", path, "--name", "discovery", "--callback-url", HOOK]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": EMAILS, "name": "discovery", "callback_url": HOOK}


def test_submit_callback_url_uppercase_scheme(respx_mock, api_key, tmp_path):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, EMAILS)
    upper = "HTTPS://hooks.example.com/mt"
    result = runner.invoke(app, ["batch", "submit", path, "--callback-url", upper])
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": EMAILS, "callback_url": upper}


@pytest.mark.parametrize(
    "url",
    ["nope", "ftp://hooks.example.com/mt", "mailto:ops@example.com", "https://", "http:///path"],
)
def test_submit_callback_url_rejects_non_http(respx_mock, guard, api_key, tmp_path, url):
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["batch", "submit", path, "--callback-url", url])
    assert result.exit_code == 2
    assert result.stderr == "Error: --callback-url must be an http(s) URL.\n"
    assert result.stdout == ""
    assert not guard.called


def test_submit_callback_url_checked_before_file(respx_mock, guard, api_key, tmp_path):
    path = tmp_path / "nope.txt"
    result = runner.invoke(app, ["batch", "submit", str(path), "--callback-url", "nope"])
    assert result.exit_code == 2
    assert result.stderr == "Error: --callback-url must be an http(s) URL.\n"
    assert not guard.called


def test_submit_callback_url_independent_of_wait(respx_mock, api_key, tmp_path, clock):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    serve(respx_mock, "batch_status_completed", STATUS)
    serve_results(respx_mock, "batch_results_json_all")
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["batch", "submit", path, "--callback-url", HOOK, "--wait"])
    assert result.exit_code == 0
    assert sent_json(route)["callback_url"] == HOOK
    assert json.loads(result.stdout) == json_rows()


def test_submit_callback_url_api_rejection(respx_mock, api_key, tmp_path):
    serve(respx_mock, "error_422_callback_url", SUBMIT, method="POST")
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["batch", "submit", path, "--callback-url", HOOK])
    msg = load_fixture("error_422_callback_url")[2]["errors"]["callback_url"][0]
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.endswith(f"Error: callback_url: {msg}\n")


def test_submit_help_lists_callback_url(respx_mock, guard, api_key):
    # A wide terminal keeps Rich from wrapping the help text mid-sentence.
    result = runner.invoke(app, ["batch", "submit", "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    assert "--callback-url" in result.stdout
    assert "URL the API calls when the batch completes." in result.stdout
    assert not guard.called


# confirmation


def test_confirm_declined(respx_mock, guard, api_key, tmp_path):
    path = write_emails(tmp_path, many(100))
    result = runner.invoke(app, ["batch", "submit", path], input="n\n")
    assert result.exit_code == 1
    assert result.stderr.startswith("This will use up to 100 credits.\nContinue? [y/N]:")
    assert result.stderr.endswith("Aborted.\n")
    assert "Submitted" not in result.stdout
    assert not guard.called


def test_confirm_accepted(respx_mock, api_key, tmp_path):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, many(100))
    result = runner.invoke(app, ["batch", "submit", path], input="y\n")
    assert result.exit_code == 0
    assert "Continue?" in result.stderr
    assert sent_json(route) == {"emails": many(100)}


@pytest.mark.parametrize("flag", ["--yes", "-y"])
def test_confirm_skipped_with_yes(respx_mock, api_key, tmp_path, flag):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    path = write_emails(tmp_path, many(100))
    result = runner.invoke(app, ["batch", "submit", path, flag])
    assert result.exit_code == 0
    assert result.stderr == "This will use up to 100 credits.\n"
    assert route.called


def test_confirm_below_threshold(respx_mock, api_key, tmp_path):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, many(99))])
    assert result.exit_code == 0
    assert "Continue?" not in result.stderr


def test_stdin_requires_yes(respx_mock, guard, api_key):
    result = runner.invoke(app, ["batch", "submit", "-"], input="\n".join(many(100)))
    assert result.exit_code == 1
    assert result.stderr.endswith(
        "Error: Pass --yes to confirm spending 100 credits when reading from stdin.\n"
    )
    assert not guard.called


def test_stdin_with_yes(respx_mock, api_key):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    result = runner.invoke(app, ["batch", "submit", "-", "--yes"], input="\n".join(many(100)))
    assert result.exit_code == 0
    assert sent_json(route) == {"emails": many(100)}


# input errors


@pytest.mark.parametrize("lines", [[], ["", "   "]])
def test_empty_file(respx_mock, guard, api_key, tmp_path, lines):
    path = tmp_path / "empty.txt"
    path.write_text("\n".join(lines))
    result = runner.invoke(app, ["batch", "submit", str(path)])
    assert result.exit_code == 1
    assert result.stderr == f"Error: No email addresses in {path}.\n"
    assert not guard.called


def test_empty_stdin(respx_mock, api_key):
    result = runner.invoke(app, ["batch", "submit", "-"], input="")
    assert result.exit_code == 1
    assert result.stderr == "Error: No email addresses in stdin.\n"


def test_too_many_addresses(respx_mock, guard, api_key, tmp_path):
    path = write_emails(tmp_path, many(10_001))
    result = runner.invoke(app, ["batch", "submit", path, "--yes"])
    assert result.exit_code == 1
    assert result.stderr == "Error: emails must hold 1 to 10,000 addresses (got 10,001)\n"
    assert not guard.called


def test_missing_file(respx_mock, guard, api_key, tmp_path):
    path = tmp_path / "nope.txt"
    result = runner.invoke(app, ["batch", "submit", str(path)])
    assert result.exit_code == 1
    assert result.stderr == f"Error: File not found: {path}\n"
    assert not guard.called


# submit --wait


def wait_routes(respx_mock, *statuses: httpx.Response):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    return respx_mock.get(STATUS).mock(side_effect=list(statuses))


def test_wait_prints_json_results(respx_mock, api_key, tmp_path, clock):
    wait_routes(
        respx_mock,
        status_response("batch_status_processing"),
        status_response("batch_status_completed"),
    )
    serve_results(respx_mock, "batch_results_json_all")
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, EMAILS), "--wait"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == json_rows()
    assert result.stderr.splitlines() == [
        "This will use up to 3 credits.",
        "Submitted batch 1457 (3 emails)",
        PROCESSING,
        COMPLETED,
    ]
    assert clock.sleeps == [3.0]


def test_wait_ignores_global_json(respx_mock, api_key, tmp_path, clock):
    wait_routes(respx_mock, status_response("batch_status_completed"))
    serve_results(respx_mock, "batch_results_json_all")
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["--json", "batch", "submit", path, "--wait"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == json_rows()


def test_wait_csv(respx_mock, api_key, tmp_path, clock):
    wait_routes(respx_mock, status_response("batch_status_completed"))
    serve_results(respx_mock, "batch_results_csv_all", format_="csv")
    path = write_emails(tmp_path, EMAILS)
    args = ["batch", "submit", path, "--wait", "--format", "csv", "--filter", "all"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert result.stdout == csv_text()


def test_wait_timeout(respx_mock, api_key, tmp_path, clock):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    serve(respx_mock, "batch_status_processing", STATUS)
    path = write_emails(tmp_path, EMAILS)
    result = runner.invoke(app, ["batch", "submit", path, "--wait", "--timeout", "5"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == (
        'Error: Batch 1457 still "processing" after 5s. Resume with: mailtester batch wait 1457'
    )
    assert sum(clock.sleeps) == 5


def test_wait_interrupted(respx_mock, api_key, tmp_path, monkeypatch):
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(MailTester, "_sleep", staticmethod(interrupt))
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    serve(respx_mock, "batch_status_processing", STATUS)
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, EMAILS), "--wait"])
    assert result.exit_code == 130
    assert result.stderr.splitlines()[-1] == (
        "Stopped waiting. Batch 1457 is still running. Resume with: mailtester batch wait 1457"
    )


# status


def test_status_processing(respx_mock, api_key):
    serve(respx_mock, "batch_status_processing", STATUS)
    result = runner.invoke(app, ["batch", "status", "1457"])
    assert result.exit_code == 0
    assert result.stdout == f"{PROCESSING}\n"


def test_status_completed(respx_mock, api_key):
    serve(respx_mock, "batch_status_completed", STATUS)
    result = runner.invoke(app, ["batch", "status", "1457"])
    assert result.exit_code == 0
    assert result.stdout == f"{COMPLETED}\n  valid 0  invalid 1  unknown 0\n"


def test_status_formats_large_counts_and_missing_values(respx_mock, api_key):
    respx_mock.get(STATUS).mock(
        side_effect=[
            status_response(
                "batch_status_processing", progress=64, processed_emails=640, total_emails=1000
            ),
            status_response("batch_status_processing", progress=None, processed_emails=None),
        ]
    )
    first = runner.invoke(app, ["batch", "status", "1457"])
    second = runner.invoke(app, ["batch", "status", "1457"])
    assert first.stdout == "Batch 1457  processing  64%  (640/1,000)\n"
    assert second.stdout == "Batch 1457  processing  -  (-/3)\n"


def test_status_json(respx_mock, api_key):
    serve(respx_mock, "batch_status_completed", STATUS)
    result = runner.invoke(app, ["--json", "batch", "status", "1457"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("batch_status_completed")


def test_status_not_found(respx_mock, api_key):
    serve(respx_mock, "error_404", f"{BASE_URL}/verify/batch/999999999")
    result = runner.invoke(app, ["batch", "status", "999999999"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: Bulk verification not found\n"


def test_status_rejects_non_numeric_id(respx_mock, guard, api_key):
    result = runner.invoke(app, ["batch", "status", "abc"])
    assert result.exit_code == 2
    assert not guard.called


# results


def test_results_default_json(respx_mock, api_key):
    serve_results(respx_mock, "batch_results_json_all")
    result = runner.invoke(app, ["batch", "results", "1457"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == json_rows()


def test_results_filter_risky(respx_mock, api_key):
    route = serve_results(respx_mock, "batch_results_json_risky", filter_="risky")
    result = runner.invoke(app, ["batch", "results", "1457", "--filter", "risky"])
    assert result.exit_code == 0
    assert route.called
    rows = json.loads(result.stdout)
    assert len(rows) == 2
    assert rows == json_rows("batch_results_json_risky")


def test_results_csv(respx_mock, api_key):
    serve_results(respx_mock, "batch_results_csv_all", format_="csv")
    result = runner.invoke(app, ["batch", "results", "1457", "--format", "csv"])
    assert result.exit_code == 0
    assert result.stdout == csv_text()


def test_results_empty(respx_mock, api_key):
    serve_results(respx_mock, "batch_results_json_valid", filter_="valid")
    result = runner.invoke(app, ["batch", "results", "1457", "--filter", "valid"])
    assert result.exit_code == 0
    assert result.stdout == "[]\n"


def test_results_ignores_global_json(respx_mock, api_key):
    serve_results(respx_mock, "batch_results_csv_all", format_="csv")
    result = runner.invoke(app, ["--json", "batch", "results", "1457", "--format", "csv"])
    assert result.stdout == csv_text()


@pytest.mark.parametrize("option", ["--filter", "--format"])
def test_results_bad_choice(respx_mock, guard, api_key, option):
    result = runner.invoke(app, ["batch", "results", "1457", option, "bogus"])
    assert result.exit_code == 2
    assert not guard.called


def test_results_not_found(respx_mock, api_key):
    serve(respx_mock, "error_404", f"{BASE_URL}/verify/batch/999999999/results")
    result = runner.invoke(app, ["batch", "results", "999999999"])
    assert result.exit_code == 1
    assert result.stderr == "Error: Bulk verification not found\n"


# review fixes


def test_confirm_prompt_keeps_stdout_clean(respx_mock, api_key, tmp_path):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, many(100))], input="y\n")
    assert result.exit_code == 0
    assert result.stdout == "Submitted batch 1457 (3 emails)\n"


def test_confirm_eof_declines(respx_mock, guard, api_key, tmp_path):
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, many(100))], input="")
    assert result.exit_code == 1
    assert result.stderr.endswith("Aborted.\n")
    assert not guard.called


@pytest.mark.parametrize(
    "data", ["josé@example.com\n".encode("latin-1"), "a@b.co".encode("utf-16")]
)
def test_non_utf8_file_fails_cleanly(respx_mock, guard, api_key, tmp_path, data):
    path = tmp_path / "emails.txt"
    path.write_bytes(data)
    result = runner.invoke(app, ["batch", "submit", str(path)])
    assert result.exit_code == 1
    assert "isn't UTF-8 text" in result.stderr
    assert "Traceback" not in result.stderr + result.stdout
    assert not guard.called


def test_stdin_bom_is_stripped(respx_mock, api_key):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    stdin = ("\ufeff" + "\n".join(EMAILS) + "\n").encode("utf-8")
    result = runner.invoke(app, ["batch", "submit", "-"], input=stdin)
    assert result.exit_code == 0
    assert sent_json(route)["emails"] == EMAILS


@pytest.mark.parametrize("extra", [["--format", "csv"], ["--filter", "risky"], ["--timeout", "60"]])
def test_wait_only_options_require_wait(respx_mock, guard, api_key, tmp_path, extra):
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, EMAILS), *extra])
    assert result.exit_code == 2
    assert "only apply with --wait" in result.stderr
    assert not guard.called


def test_wait_api_error_points_to_status(respx_mock, api_key, tmp_path, clock):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    serve(respx_mock, "error_502", STATUS)
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, EMAILS), "--wait"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == (
        "Error: The deliverability engine is currently unavailable. "
        "Batch 1457 may still be running. Check it with: mailtester batch status 1457"
    )


def test_wait_results_error_points_to_results(respx_mock, api_key, tmp_path, clock):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    serve(respx_mock, "batch_status_completed", STATUS)
    serve(respx_mock, "error_500", RESULTS)
    result = runner.invoke(app, ["batch", "submit", write_emails(tmp_path, EMAILS), "--wait"])
    assert result.exit_code == 1
    assert result.stderr.splitlines()[-1] == (
        "Error: Verification failed: SMTP connection error. "
        "Batch 1457 is complete. Fetch it with: mailtester batch results 1457"
    )


# 012 discovery


def test_status_pending(respx_mock, api_key):
    serve(respx_mock, "batch_status_pending", STATUS)
    result = runner.invoke(app, ["batch", "status", "1457"])
    assert result.exit_code == 0
    assert result.stdout == "Batch 1492  pending  0%  (0/1)\n"


def test_status_fractional_progress(respx_mock, api_key):
    serve(respx_mock, "batch_status_partial", STATUS)
    result = runner.invoke(app, ["batch", "status", "1457"])
    assert result.exit_code == 0
    assert result.stdout == "Batch 1491  processing  12.5%  (1/8)\n"


def test_results_not_complete_points_to_status(respx_mock, api_key):
    serve(respx_mock, "batch_results_not_complete", RESULTS)
    result = runner.invoke(app, ["batch", "results", "1457"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: Bulk verification is not complete yet. "
        "Check it with: mailtester batch status 1457\n"
    )


def test_results_other_errors_have_no_status_hint(respx_mock, api_key):
    serve(respx_mock, "error_500", RESULTS)
    result = runner.invoke(app, ["batch", "results", "1457"])
    assert result.exit_code == 1
    assert result.stderr == "Error: Verification failed: SMTP connection error\n"


# batch wait


def test_wait_command_prints_json_results(respx_mock, api_key, clock):
    respx_mock.get(STATUS).mock(
        side_effect=[
            status_response("batch_status_processing"),
            status_response("batch_status_completed"),
        ]
    )
    serve_results(respx_mock, "batch_results_json_all")
    result = runner.invoke(app, ["batch", "wait", "1457"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == json_rows()
    assert result.stderr.splitlines() == [PROCESSING, COMPLETED]
    assert clock.sleeps == [3.0]


def test_wait_command_completed_immediately_prints_one_line(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_completed", STATUS)
    serve_results(respx_mock, "batch_results_json_all")
    result = runner.invoke(app, ["batch", "wait", "1457"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == json_rows()
    assert result.stderr == f"{COMPLETED}\n"
    assert clock.sleeps == []


def test_wait_command_csv(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_completed", STATUS)
    serve_results(respx_mock, "batch_results_csv_all", format_="csv")
    args = ["batch", "wait", "1457", "--format", "csv", "--filter", "all"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert result.stdout == csv_text()


def test_wait_command_filter_risky(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_completed", STATUS)
    route = serve_results(respx_mock, "batch_results_json_risky", filter_="risky")
    result = runner.invoke(app, ["batch", "wait", "1457", "--filter", "risky"])
    assert result.exit_code == 0
    assert route.called
    rows = json.loads(result.stdout)
    assert len(rows) == 2
    assert rows == json_rows("batch_results_json_risky")


def test_wait_command_ignores_global_json(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_completed", STATUS)
    serve_results(respx_mock, "batch_results_csv_all", format_="csv")
    result = runner.invoke(app, ["--json", "batch", "wait", "1457", "--format", "csv"])
    assert result.exit_code == 0
    assert result.stdout == csv_text()


def test_wait_command_pending_fails_fast(respx_mock, api_key, clock):
    route = serve(respx_mock, "batch_status_pending", f"{BASE_URL}/verify/batch/1492")
    result = runner.invoke(app, ["batch", "wait", "1492"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: Batch 1492 is pending and won't start on its own. "
        "Start it in the MailTester app, then run this again.\n"
    )
    assert route.call_count == 1
    assert clock.sleeps == []


def test_wait_command_timeout(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_processing", STATUS)
    result = runner.invoke(app, ["batch", "wait", "1457", "--timeout", "5"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == (
        'Error: Batch 1457 still "processing" after 5s. Resume with: mailtester batch wait 1457'
    )
    assert sum(clock.sleeps) == 5


def test_wait_command_not_found(respx_mock, api_key, clock):
    route = serve(respx_mock, "error_404", f"{BASE_URL}/verify/batch/999999999")
    result = runner.invoke(app, ["batch", "wait", "999999999"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: Bulk verification not found\n"
    assert route.call_count == 1


def test_wait_command_api_error_says_resume(respx_mock, api_key, clock):
    serve(respx_mock, "error_502", STATUS)
    result = runner.invoke(app, ["batch", "wait", "1457"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == (
        "Error: The deliverability engine is currently unavailable. "
        "Batch 1457 may still be running. Resume with: mailtester batch wait 1457"
    )


def test_wait_command_results_error_points_to_results(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_completed", STATUS)
    serve(respx_mock, "error_500", RESULTS)
    result = runner.invoke(app, ["batch", "wait", "1457"])
    assert result.exit_code == 1
    assert result.stderr.splitlines()[-1] == (
        "Error: Verification failed: SMTP connection error. "
        "Batch 1457 is complete. Fetch it with: mailtester batch results 1457"
    )


def test_wait_command_failed_status(respx_mock, api_key, clock):
    respx_mock.get(STATUS).mock(
        return_value=status_response("batch_status_processing", status="failed")
    )
    result = runner.invoke(app, ["batch", "wait", "1457"])
    assert result.exit_code == 1
    assert result.stdout == ""
    # The failed poll prints its status line first, as any successful poll does.
    assert result.stderr.splitlines()[-1] == 'Error: Batch 1457 ended with status "failed"'


def test_wait_command_interrupted(respx_mock, api_key, monkeypatch):
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(MailTester, "_sleep", staticmethod(interrupt))
    serve(respx_mock, "batch_status_processing", STATUS)
    result = runner.invoke(app, ["batch", "wait", "1457"])
    assert result.exit_code == 130
    assert result.stderr.splitlines()[-1] == (
        "Stopped waiting. Batch 1457 is still running. Resume with: mailtester batch wait 1457"
    )


@pytest.mark.parametrize("option", ["--filter", "--format"])
def test_wait_command_bad_choice(respx_mock, guard, api_key, option):
    result = runner.invoke(app, ["batch", "wait", "1457", option, "bogus"])
    assert result.exit_code == 2
    assert not guard.called


def test_wait_command_rejects_non_numeric_id(respx_mock, guard, api_key):
    result = runner.invoke(app, ["batch", "wait", "abc"])
    assert result.exit_code == 2
    assert not guard.called


def test_batch_help_order(respx_mock, guard, api_key):
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    names = [
        line.split()[1]
        for line in result.stdout.splitlines()
        if line.startswith("│ ") and not line.startswith("│ --")
    ]
    assert names == ["submit", "status", "wait", "results"]
    assert not guard.called

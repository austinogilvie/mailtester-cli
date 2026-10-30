import copy
import json
import re

import pytest
from conftest import load_fixture, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL, MailTester

LISTS = f"{BASE_URL}/lists"
HEADER = "ID    NAME       STATUS     EMAILS  VALID  INVALID  RISKY  CREATED"
# The NAME column is as wide as the longest name; the recorded lists are "redacted list".
WIDE_HEADER = "ID    NAME           STATUS     EMAILS  VALID  INVALID  RISKY  CREATED"
ROW_PAGE_2 = "1491  redacted list  completed       8      1        2      5  2026-10-29"
ROW_COMPLETED = "1498  redacted list  completed       2      0        2      0  2026-10-29"
ROW_PENDING = "1558  discovery      pending         1      0        0      0  2026-10-30"
OPTIONS_ERROR = "Error: --page, --per-page, and --all apply to `mailtester lists` only.\n"

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


@pytest.fixture
def guard(respx_mock):
    """A catch-all route: any request at all shows up as guard.called."""
    return respx_mock.route().respond(500)


def fixture_data(name: str):
    return copy.deepcopy(load_fixture(name)[2]["data"])


def respond(respx_mock, url: str, data) -> None:
    respx_mock.get(url).respond(200, json={"success": True, "data": data})


def page_body(items: list[dict], **meta) -> dict:
    base = {"current_page": 1, "last_page": 1, "per_page": 25, "total": len(items)}
    return {"success": True, "data": items, "meta": {**base, **meta}}


def serve_page(respx_mock, body: dict, params: dict[str, str] | None = None):
    """Route GET /lists to `body`; with `params`, only a request with exactly that query."""
    patterns = {"params__eq": params} if params is not None else {}
    return respx_mock.get(LISTS, **patterns).respond(200, json=body)


def item_completed() -> dict:
    return fixture_data("list_show")


def item_pending() -> dict:
    return fixture_data("list_show_pending")


def delete_url(list_id: int) -> str:
    return f"{LISTS}/{list_id}"


def serve_deleted(respx_mock, list_id: int):
    return respx_mock.delete(delete_url(list_id)).respond(
        200, json={"success": True, "message": "List deleted"}
    )


SHOW_COMPLETED = (
    "List 1498  redacted list  completed  100%  (2/2)\n"
    "  valid       0\n"
    "  invalid     2\n"
    "  risky       0\n"
    "  unknown     0\n"
    "  credits     2\n"
    "  created     2026-10-29 14:56:13 UTC\n"
    "  started     2026-10-29 14:56:13 UTC\n"
    "  completed   2026-10-29 14:56:39 UTC\n"
)
SHOW_PENDING = (
    "List 1558  -  pending  0%  (0/1)\n"
    "  valid       0\n"
    "  invalid     0\n"
    "  risky       0\n"
    "  unknown     0\n"
    "  credits     1\n"
    "  created     2026-10-30 17:40:51 UTC\n"
    "  started     -\n"
    "  completed   -\n"
)


# lists


def test_lists_table(respx_mock, api_key):
    route = serve(respx_mock, "lists_after_batch", LISTS, params__eq={"per_page": "5"})
    result = runner.invoke(app, ["lists", "--per-page", "5"])
    assert result.exit_code == 0
    assert result.stdout == (
        f"{HEADER}\n1457  discovery  completed       3      0        1      2  2026-10-29\n"
    )
    assert route.called


def test_lists_sends_no_query_by_default(respx_mock, api_key):
    route = serve(respx_mock, "lists_after_batch", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert route.calls.last.request.url.query == b""


def test_lists_empty(respx_mock, api_key):
    serve(respx_mock, "lists", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert result.stdout == "No lists yet.\n"


@pytest.mark.parametrize("name", ["lists_after_batch", "lists"])
def test_lists_json(respx_mock, api_key, name):
    serve(respx_mock, name, LISTS)
    result = runner.invoke(app, ["--json", "lists"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data(name)


def test_lists_missing_name_and_large_counts(respx_mock, api_key):
    data = fixture_data("lists_after_batch")
    data[0].update(name=None, total_emails=1000, created_at="2026-10-01T23:30:00-02:00")
    body = {**load_fixture("lists_after_batch")[2], "data": data}  # keeps the real meta block
    respx_mock.get(LISTS).respond(200, json=body)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ID    NAME  STATUS     EMAILS  VALID  INVALID  RISKY  CREATED",
        "1457  -     completed   1,000      0        1      2  2026-10-02",
    ]


def test_lists_rejects_out_of_range_per_page(api_key):
    assert runner.invoke(app, ["lists", "--per-page", "0"]).exit_code == 2
    assert runner.invoke(app, ["lists", "--per-page", "101"]).exit_code == 2


def test_lists_api_error(respx_mock, api_key):
    serve(respx_mock, "error_401", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: Invalid API key\n"


# pages


def test_lists_page_sends_page(respx_mock, api_key):
    route = serve(respx_mock, "lists_page_2", LISTS, params__eq={"per_page": "1", "page": "2"})
    result = runner.invoke(app, ["lists", "--per-page", "1", "--page", "2"])
    assert result.exit_code == 0
    assert result.stdout == f"{WIDE_HEADER}\n{ROW_PAGE_2}\n"
    assert result.stderr == "Page 2 of 3 (3 lists). Use --page 3 or --all for more.\n"
    assert route.called


def test_lists_single_page_has_no_hint(respx_mock, api_key):
    serve(respx_mock, "lists_after_batch", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert result.stderr == ""


def test_lists_page_hint_on_stderr(respx_mock, api_key):
    route = serve_page(respx_mock, page_body([item_completed()], last_page=3, total=57))
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert result.stdout == f"{WIDE_HEADER}\n{ROW_COMPLETED}\n"
    assert result.stderr == "Page 1 of 3 (57 lists). Use --page 2 or --all for more.\n"
    assert route.calls.last.request.url.query == b""


def test_lists_hint_names_next_page(respx_mock, api_key):
    serve_page(
        respx_mock,
        page_body([item_completed()], current_page=2, last_page=3, total=1234),
        params={"page": "2"},
    )
    result = runner.invoke(app, ["lists", "--page", "2"])
    assert result.exit_code == 0
    assert result.stderr == "Page 2 of 3 (1,234 lists). Use --page 3 or --all for more.\n"


def test_lists_last_page_has_no_hint(respx_mock, api_key):
    serve_page(
        respx_mock,
        page_body([item_completed()], current_page=3, last_page=3, total=57),
        params={"page": "3"},
    )
    result = runner.invoke(app, ["lists", "--page", "3"])
    assert result.exit_code == 0
    assert result.stdout == f"{WIDE_HEADER}\n{ROW_COMPLETED}\n"
    assert result.stderr == ""


def test_lists_json_prints_rows_and_keeps_hint(respx_mock, api_key):
    serve_page(respx_mock, page_body([item_completed()], last_page=3, total=57))
    result = runner.invoke(app, ["--json", "lists"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == [item_completed()]
    assert result.stderr == "Page 1 of 3 (57 lists). Use --page 2 or --all for more.\n"


def test_lists_past_last_page(respx_mock, api_key):
    serve(respx_mock, "lists_past_last_page", LISTS, params__eq={"per_page": "1", "page": "4"})
    result = runner.invoke(app, ["lists", "--per-page", "1", "--page", "4"])
    assert result.exit_code == 0
    assert result.stdout == "No lists on page 4 (last page is 3).\n"
    assert result.stderr == ""


def test_lists_past_last_page_json_prints_empty_list(respx_mock, api_key):
    serve(respx_mock, "lists_past_last_page", LISTS, params__eq={"per_page": "1", "page": "4"})
    result = runner.invoke(app, ["--json", "lists", "--per-page", "1", "--page", "4"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert result.stderr == ""


def test_lists_empty_with_page_says_no_lists_yet(respx_mock, api_key):
    serve_page(respx_mock, page_body([], current_page=4), params={"page": "4"})
    result = runner.invoke(app, ["lists", "--page", "4"])
    assert result.exit_code == 0
    assert result.stdout == "No lists yet.\n"


# --all


def _all_routes(respx_mock, per_page: int):
    size = str(per_page)
    p1 = serve_page(
        respx_mock,
        page_body([item_pending()], last_page=2, per_page=per_page, total=2),
        params={"per_page": size, "page": "1"},
    )
    p2 = serve_page(
        respx_mock,
        page_body([item_completed()], current_page=2, last_page=2, per_page=per_page, total=2),
        params={"per_page": size, "page": "2"},
    )
    return p1, p2


def test_lists_all_walks_pages(respx_mock, api_key):
    p1, p2 = _all_routes(respx_mock, 100)
    result = runner.invoke(app, ["lists", "--all"])
    assert result.exit_code == 0
    assert result.stdout == f"{WIDE_HEADER}\n{ROW_PENDING}\n{ROW_COMPLETED}\n"
    assert result.stderr == ""
    assert p1.call_count == 1 and p2.call_count == 1


def test_lists_all_uses_per_page(respx_mock, api_key):
    p1, p2 = _all_routes(respx_mock, 1)
    result = runner.invoke(app, ["lists", "--all", "--per-page", "1"])
    assert result.exit_code == 0
    assert result.stdout == f"{WIDE_HEADER}\n{ROW_PENDING}\n{ROW_COMPLETED}\n"
    assert p1.call_count == 1 and p2.call_count == 1


def test_lists_all_json_concatenates_rows(respx_mock, api_key):
    _all_routes(respx_mock, 100)
    result = runner.invoke(app, ["--json", "lists", "--all"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == [item_pending(), item_completed()]
    assert result.stderr == ""


def test_lists_all_empty(respx_mock, api_key):
    serve(respx_mock, "lists", LISTS, params__eq={"per_page": "100", "page": "1"})
    result = runner.invoke(app, ["lists", "--all"])
    assert result.exit_code == 0
    assert result.stdout == "No lists yet.\n"


def test_lists_all_with_page_is_usage_error(api_key, guard):
    result = runner.invoke(app, ["lists", "--all", "--page", "2"])
    assert result.exit_code == 2
    assert result.stderr == "Error: --all and --page can't be combined.\n"
    assert result.stdout == ""
    assert not guard.called


def test_lists_rejects_page_zero(api_key, guard):
    result = runner.invoke(app, ["lists", "--page", "0"])
    assert result.exit_code == 2
    assert not guard.called


@pytest.mark.parametrize("opts", [["--page", "2"], ["--per-page", "5"], ["--all"]])
def test_lists_options_before_subcommand_are_rejected(api_key, guard, opts):
    result = runner.invoke(app, ["lists", *opts, "show", "1457"])
    assert result.exit_code == 2
    assert result.stderr == OPTIONS_ERROR
    assert result.stdout == ""
    assert not guard.called


# show


def test_lists_show_human(respx_mock, api_key):
    serve(respx_mock, "list_show", f"{LISTS}/1498")
    result = runner.invoke(app, ["lists", "show", "1498"])
    assert result.exit_code == 0
    assert result.stdout == SHOW_COMPLETED
    assert result.stderr == ""


def test_lists_show_pending_prints_dashes(respx_mock, api_key):
    data = item_pending()
    data["name"] = None  # an upload without --name has no name
    respond(respx_mock, f"{LISTS}/1558", data)
    result = runner.invoke(app, ["lists", "show", "1558"])
    assert result.exit_code == 0
    assert result.stdout == SHOW_PENDING


def test_lists_show_fractional_progress_and_commas(respx_mock, api_key):
    data = item_completed()
    data.update(
        status="processing",
        progress=12.5,
        total_emails=5400,
        processed_emails=675,
        created_at="2026-10-01T23:30:00-02:00",
    )
    data["results"]["valid"] = 1000
    respond(respx_mock, f"{LISTS}/1498", data)
    result = runner.invoke(app, ["lists", "show", "1498"])
    assert result.exit_code == 0
    assert result.stdout.splitlines()[:2] == [
        "List 1498  redacted list  processing  12.5%  (675/5,400)",
        "  valid       1,000",
    ]
    assert result.stdout.splitlines()[6] == "  created     2026-10-02 01:30:00 UTC"


def test_lists_show_json(respx_mock, api_key):
    serve(respx_mock, "list_show", f"{LISTS}/1457")
    result = runner.invoke(app, ["--json", "lists", "show", "1457"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == item_completed()
    assert result.stderr == ""


def test_lists_show_not_found(respx_mock, api_key):
    serve(respx_mock, "error_404_list", f"{LISTS}/999999999")
    result = runner.invoke(app, ["lists", "show", "999999999"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {load_fixture('error_404_list')[2]['error']}\n"


def test_lists_show_rejects_non_numeric_id(api_key, guard):
    result = runner.invoke(app, ["lists", "show", "abc"])
    assert result.exit_code == 2
    assert not guard.called


def test_lists_show_does_not_fetch_table(respx_mock, api_key):
    serve(respx_mock, "list_show", f"{LISTS}/1457")
    table = respx_mock.get(LISTS).respond(500)
    result = runner.invoke(app, ["lists", "show", "1457"])
    assert result.exit_code == 0
    assert table.call_count == 0


# delete


def test_delete_prompts_and_deletes_one(respx_mock, api_key):
    route = serve(respx_mock, "list_delete", delete_url(1492), method="DELETE")
    result = runner.invoke(app, ["lists", "delete", "1492"], input="y\n")
    assert result.exit_code == 0
    assert result.stderr == "Delete list 1492? [y/N]: "
    assert result.stdout == "Deleted list 1492\n"
    assert route.call_count == 1
    assert route.calls.last.request.content == b""


def test_delete_prompts_for_several(respx_mock, api_key):
    first = serve_deleted(respx_mock, 1492)
    second = serve_deleted(respx_mock, 1493)
    result = runner.invoke(app, ["lists", "delete", "1492", "1493"], input="y\n")
    assert result.exit_code == 0
    assert result.stderr == "Delete 2 lists (1492, 1493)? [y/N]: "
    assert result.stdout == "Deleted list 1492\nDeleted list 1493\n"
    assert first.call_count == 1 and second.call_count == 1


def test_delete_declined(api_key, guard):
    result = runner.invoke(app, ["lists", "delete", "1492"], input="n\n")
    assert result.exit_code == 1
    assert result.stderr == "Delete list 1492? [y/N]: Aborted.\n"
    assert result.stdout == ""
    assert not guard.called


def test_delete_eof_declines(api_key, guard):
    result = runner.invoke(app, ["lists", "delete", "1492"], input="")
    assert result.exit_code == 1
    assert result.stderr == "Delete list 1492? [y/N]: \nAborted.\n"
    assert not guard.called


@pytest.mark.parametrize("flag", ["--yes", "-y"])
def test_delete_yes_skips_prompt(respx_mock, api_key, flag):
    serve(respx_mock, "list_delete", delete_url(1492), method="DELETE")
    result = runner.invoke(app, ["lists", "delete", "1492", flag])
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == "Deleted list 1492\n"


def test_delete_continues_after_failure(respx_mock, api_key):
    missing = serve(respx_mock, "error_404_list", delete_url(1492), method="DELETE")
    deleted = serve_deleted(respx_mock, 1493)
    result = runner.invoke(app, ["lists", "delete", "1492", "1493", "--yes"])
    assert result.exit_code == 1
    assert result.stdout == "Deleted list 1493\n"
    assert result.stderr == f"Error: List 1492: {load_fixture('error_404_list')[2]['error']}\n"
    assert missing.call_count == 1 and deleted.call_count == 1


def test_delete_single_failure_has_no_prefix(respx_mock, api_key):
    serve(respx_mock, "error_409_list_processing", delete_url(1457), method="DELETE")
    result = runner.invoke(app, ["lists", "delete", "1457", "--yes"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (f"Error: {load_fixture('error_409_list_processing')[2]['error']}\n")


def test_delete_stops_on_rate_limit(respx_mock, api_key):
    serve(respx_mock, "error_429", delete_url(1492), method="DELETE")
    second = serve_deleted(respx_mock, 1493)
    result = runner.invoke(app, ["lists", "delete", "1492", "1493", "--yes"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {load_fixture('error_429')[2]['error']}. Retry in 30s.\n"
    assert second.call_count == 0


def test_delete_stops_on_bad_key(respx_mock, api_key):
    serve(respx_mock, "error_401", delete_url(1492), method="DELETE")
    second = serve_deleted(respx_mock, 1493)
    result = runner.invoke(app, ["lists", "delete", "1492", "1493", "--yes"])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: ")
    assert second.call_count == 0


def test_delete_success_then_rate_limit_keeps_earlier_output(respx_mock, api_key):
    serve_deleted(respx_mock, 1492)
    serve(respx_mock, "error_429", delete_url(1493), method="DELETE")
    result = runner.invoke(app, ["lists", "delete", "1492", "1493", "--yes"])
    assert result.exit_code == 1
    assert result.stdout == "Deleted list 1492\n"


def test_delete_dedupes_ids(respx_mock, api_key):
    route = serve(respx_mock, "list_delete", delete_url(1492), method="DELETE")
    result = runner.invoke(app, ["lists", "delete", "1492", "1492"], input="y\n")
    assert result.stderr == "Delete list 1492? [y/N]: "
    assert result.stdout == "Deleted list 1492\n"
    assert route.call_count == 1


def test_delete_requires_an_id(api_key, guard):
    result = runner.invoke(app, ["lists", "delete"])
    assert result.exit_code == 2
    assert not guard.called


def test_delete_rejects_non_numeric_id(api_key, guard):
    result = runner.invoke(app, ["lists", "delete", "abc"])
    assert result.exit_code == 2
    assert not guard.called


def test_delete_ignores_global_json(respx_mock, api_key):
    serve(respx_mock, "list_delete", delete_url(1492), method="DELETE")
    result = runner.invoke(app, ["--json", "lists", "delete", "1492", "--yes"])
    assert result.exit_code == 0
    assert result.stdout == "Deleted list 1492\n"


def test_delete_interrupted_exits_130(api_key, monkeypatch):
    def boom(self, list_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(MailTester, "delete_list", boom)
    result = runner.invoke(app, ["lists", "delete", "1492", "--yes"])
    assert result.exit_code == 130
    assert result.stderr == "Interrupted.\n"
    assert result.stdout == ""


def test_delete_missing_key(guard):
    result = runner.invoke(app, ["lists", "delete", "1492", "--yes"])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: ")
    assert "MAILTESTER_API_KEY" in result.stderr
    assert not guard.called


def test_delete_help():
    result = runner.invoke(app, ["lists", "delete", "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    assert "--yes" in result.stdout
    assert "-y" in result.stdout
    assert "Skip the confirmation prompt." in result.stdout
    assert "Delete one or more lists and their results. (free)" in result.stdout


# help


def listed_commands(help_text: str) -> list[str]:
    section = help_text.split("Commands", 1)[1]
    return re.findall(r"^│ (\w+)\s", section, flags=re.MULTILINE)


def test_help_lists_account_commands_after_find():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    section = result.stdout.split("Commands", 1)[1]
    listed = re.findall(r"^│ (\w+)\s", section, flags=re.MULTILINE)
    assert listed.index("find") < listed.index("lists") < listed.index("credits")
    assert "List, inspect, and delete your bulk verification jobs." in result.stdout
    assert "Show your credit balance." in result.stdout


def test_lists_help_shows_options_and_show():
    result = runner.invoke(app, ["lists", "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    assert "--page" in result.stdout
    assert "--per-page" in result.stdout
    assert "--all" in result.stdout
    assert "Page to show (default 1)." in result.stdout
    assert "Fetch every page." in result.stdout
    assert "Show one list's status and counts. (free)" in result.stdout


def test_lists_help_order():
    result = runner.invoke(app, ["lists", "--help"])
    assert result.exit_code == 0
    assert listed_commands(result.stdout) == ["show", "delete"]

"""Capture live MailTester API responses as test fixtures (002-FEAT-API-DISCOVERY).

Free calls run by default. Paid calls run only with --spend.

    uv run python scripts/discover.py
    uv run python scripts/discover.py --spend \\
        --verify-email someone@company.com \\
        --find Jane Doe company.com \\
        --batch good@company.com nobody@company.com info@company.com

Pass --batch-id to rerun the free batch reads on an existing batch. --batch is then used
only for redaction and nothing is submitted.

Real addresses, names, and domains are replaced with placeholders before anything is
written. The API key is never written or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

BASE_URL = "https://app.mailtester.com/api/v1"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "api"
KEPT_HEADERS = (
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",
    "content-type",
)
PLACEHOLDER_DOMAINS = ("example.com", "example.net", "example.org")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled", "error"}
VERIFY_COST, FIND_COST = 1, 3


class Stop(Exception):
    """A paid step returned something unexpected; stop before spending more."""


def _match_case(original: str, replacement: str) -> str:
    if len(original) > 1 and original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:].lower()
    return replacement.lower()


def _replace_name(pattern: str, fake: str, s: str) -> str:
    return re.sub(pattern, lambda m: _match_case(m.group(), fake), s, flags=re.I)


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_") or "unknown"


class Redactor:
    """Swaps real inputs for stable placeholders and reports anything that slips through."""

    def __init__(self) -> None:
        self.emails: dict[str, str] = {}
        self.domains: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.secrets: dict[str, str] = {}
        self._swept: dict[str, str] = {}

    def domain(self, real: str) -> str:
        real = real.lower()
        if real not in self.domains:
            i = len(self.domains)
            fake = PLACEHOLDER_DOMAINS[i] if i < len(PLACEHOLDER_DOMAINS) else f"domain{i}.example"
            self.domains[real] = fake
        return self.domains[real]

    def email(self, real: str, local: str) -> str:
        real = real.lower()
        self.emails[real] = f"{local}@{self.domain(real.rpartition('@')[2])}"
        return self.emails[real]

    def name(self, real: str, placeholder: str) -> None:
        self.names[real.lower()] = placeholder

    def secret(self, value: str, label: str) -> None:
        self.secrets[value] = label

    def text(self, s: str) -> str:
        for real, fake in self.emails.items():
            # Bounded, so "torvalds@x.org" doesn't match inside "linus.torvalds@x.org".
            s = re.sub(rf"(?<![\w.%+-]){re.escape(real)}(?![\w.-])", fake, s, flags=re.I)
        for real, fake in self.domains.items():
            s = re.sub(rf"(?<![\w-]){re.escape(real)}(?![\w-])", fake, s, flags=re.I)
        s = EMAIL_RE.sub(lambda m: self._names_in_local(m.group()), s)
        for real, fake in self._names_longest_first():
            s = _replace_name(rf"\b{re.escape(real)}\b", fake, s)
        return EMAIL_RE.sub(lambda m: self._sweep(m.group()), s)

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {self.text(k): self.obj(v) for k, v in o.items()}
        if isinstance(o, list):
            return [self.obj(v) for v in o]
        return o

    def leaks(self, s: str) -> list[str]:
        low = s.lower()
        return sorted({label for token, label in self._tokens().items() if token.lower() in low})

    def _names_longest_first(self) -> list[tuple[str, str]]:
        return sorted(self.names.items(), key=lambda kv: -len(kv[0]))

    def _names_in_local(self, email: str) -> str:
        # Names inside a local part ("jdoe", "john.doe") aren't word-bounded, so match substrings.
        local, _, domain = email.rpartition("@")
        for real, fake in self._names_longest_first():
            local = _replace_name(re.escape(real), fake, local)
        return f"{local}@{domain}"

    def _sweep(self, email: str) -> str:
        domain = email.rpartition("@")[2].lower()
        safe = set(self.domains.values()) | {"example.invalid"}
        if domain in safe or domain.endswith(".example"):
            return email
        key = email.lower()
        if key not in self._swept:
            self._swept[key] = f"redacted{len(self._swept) + 1}@example.invalid"
        return self._swept[key]

    def _tokens(self) -> dict[str, str]:
        tokens = {"evp_": "evp_"}
        for email in self.emails:
            tokens[email] = email
            local = email.partition("@")[0]
            if len(local) >= 3:
                tokens[local] = local
        for domain in self.domains:
            tokens[domain] = domain
            label = domain.partition(".")[0]
            if len(label) >= 3:
                tokens[label] = label
        tokens.update({n: n for n in self.names if len(n) >= 3})
        tokens.update(self.secrets)
        return tokens


def _scrub_list_names(o: Any) -> Any:
    """List names in the account can hold customer data; keep only our own batch's name."""
    if isinstance(o, dict):
        return {
            k: "redacted list"
            if k == "name" and isinstance(v, str) and v != "discovery"
            else _scrub_list_names(v)
            for k, v in o.items()
        }
    if isinstance(o, list):
        return [_scrub_list_names(v) for v in o]
    return o


class Recorder:
    """Makes requests and writes each response as a redacted fixture."""

    def __init__(self, client: httpx.Client, redactor: Redactor, out: Path) -> None:
        self.client = client
        self.redactor = redactor
        self.out = out
        self.written: list[Path] = []

    def call(
        self,
        step: str,
        name: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        client: httpx.Client | None = None,
        scrub_names: bool = False,
    ) -> httpx.Response:
        resp = (client or self.client).request(method, path, params=params, json=json_body)
        self.save(step, name, resp, params=params, json_body=json_body, scrub_names=scrub_names)
        return resp

    def save(
        self,
        step: str,
        name: str,
        resp: httpx.Response,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        scrub_names: bool = False,
    ) -> None:
        request = resp.request
        record: dict[str, Any] = {
            "request": {
                "method": request.method,
                "path": request.url.path.removeprefix("/api/v1"),
                "params": params or {},
                "json": json_body,
            },
            "status": resp.status_code,
            "headers": {k: resp.headers[k] for k in KEPT_HEADERS if k in resp.headers},
        }
        try:
            body = resp.json()
        except ValueError:
            ext = ".csv" if "csv" in resp.headers.get("content-type", "") else ".txt"
            body_path = self.out / f"{name}{ext}"
            body_path.write_text(self.redactor.text(resp.text))
            self.written.append(body_path)
            record["body_file"] = body_path.name
        else:
            record["body"] = _scrub_list_names(body) if scrub_names else body
        record["synthetic"] = False

        path = self.out / f"{name}.json"
        path.write_text(json.dumps(self.redactor.obj(record), indent=2, ensure_ascii=False) + "\n")
        self.written.append(path)
        route = f"{request.method} {record['request']['path']}"
        print(f"[{step:>3}] {route} -> {resp.status_code}  {name}")

    def leaks(self) -> dict[str, list[str]]:
        found = {}
        for path in self.written:
            hits = self.redactor.leaks(path.read_text())
            if hits:
                found[path.name] = hits
        return found


def _data(resp: httpx.Response) -> Any:
    try:
        return resp.json().get("data")
    except (ValueError, AttributeError):
        return None


def run_paid(rec: Recorder, args: argparse.Namespace) -> int | None:
    def expect_ok(step: str, resp: httpx.Response) -> None:
        if not resp.is_success:
            raise Stop(f"step {step} returned {resp.status_code}; not running further paid steps")

    if args.verify_email:
        body = {"email": args.verify_email, "smtp_check": True}
        expect_ok("3", rec.call("3", "verify_single", "POST", "/verify", json_body=body))
    if args.find:
        first, last, domain = args.find
        body = {"first_name": first, "last_name": last, "domain": domain}
        expect_ok("4", rec.call("4", "finder_email", "POST", "/finder/email", json_body=body))
    if args.find_missing:
        first, last, domain = args.find_missing
        body = {"first_name": first, "last_name": last, "domain": domain}
        resp = rec.call("4b", "finder_email_missing", "POST", "/finder/email", json_body=body)
        if not resp.is_success and resp.status_code != 404:
            expect_ok("4b", resp)
    if args.batch and not args.batch_id:
        body = {"emails": args.batch, "name": "discovery", "auto_start": True}
        resp = rec.call("5", "batch_submit", "POST", "/verify/batch", json_body=body)
        expect_ok("5", resp)
        data = _data(resp)
        if not isinstance(data, dict) or "id" not in data:
            raise Stop(f"step 5 response has no data.id (data: {data!r})")
        return data["id"]
    return None


def poll_batch(rec: Recorder, batch_id: int | str, timeout: float) -> str | None:
    seen: set[str] = set()
    deadline = time.monotonic() + timeout
    delay = 3.0
    while True:
        resp = rec.client.get(f"/verify/batch/{batch_id}")
        if resp.status_code == 429:
            retry_after = float((resp.json() or {}).get("retry_after") or 10)
            print(f"[  6] rate limited, waiting {retry_after:.0f}s")
            time.sleep(retry_after)
            continue
        data = _data(resp)
        status = data.get("status") if isinstance(data, dict) else None
        if str(status) not in seen:
            seen.add(str(status))
            rec.save("6", f"batch_status_{_slug(status)}", resp)
        if not resp.is_success or status in TERMINAL_STATUSES:
            return status
        if time.monotonic() > deadline:
            print(f"[  6] gave up waiting after {timeout:.0f}s (last status: {status})")
            return status
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def run_batch_reads(rec: Recorder, args: argparse.Namespace, batch_id: int | str) -> None:
    poll_batch(rec, batch_id, args.poll_timeout)
    path = f"/verify/batch/{batch_id}/results"
    rec.call("7", "batch_results_json_all", "GET", path, params={"format": "json", "filter": "all"})
    for fmt in args.formats:
        params = {"format": fmt, "filter": "all"}
        rec.call("8", f"batch_results_{_slug(fmt)}_all", "GET", path, params=params)
    for flt in args.filters:
        params = {"format": "json", "filter": flt}
        rec.call("8", f"batch_results_json_{_slug(flt)}", "GET", path, params=params)
    bogus = {"format": "bogus", "filter": "all"}
    rec.call("8", "batch_results_bogus_format", "GET", path, params=bogus)
    bogus = {"format": "json", "filter": "bogus"}
    rec.call("8", "batch_results_bogus_filter", "GET", path, params=bogus)
    params = {"per_page": 5}
    rec.call("9", "lists_after_batch", "GET", "/lists", params=params, scrub_names=True)


def run_errors(rec: Recorder) -> None:
    bad_key = httpx.Client(base_url=BASE_URL, headers={"Authorization": "Bearer evp_invalid"})
    with bad_key:
        rec.call("11", "error_401", "GET", "/credits", client=bad_key)
    rec.call("12", "error_404", "GET", "/verify/batch/999999999")
    rec.call("13", "error_422", "POST", "/verify/batch", json_body={"emails": []})


def build_redactor(args: argparse.Namespace, key: str) -> Redactor:
    r = Redactor()
    r.secret(key, "API key")
    # Finder domain first, so it's the one that becomes example.com.
    if args.find:
        first, last, domain = args.find
        r.domain(domain)
        r.name(first, "Jane")
        r.name(last, "Doe")
    if args.find_missing:
        first, last, domain = args.find_missing
        r.domain(domain)
        r.name(first, "Nobody")
        r.name(last, "Missing")
    if args.verify_email:
        r.email(args.verify_email, "verify")
    for i, email in enumerate(args.batch or [], 1):
        r.email(email, f"batch{i}")
    return r


def planned_cost(args: argparse.Namespace) -> int:
    cost = VERIFY_COST if args.verify_email else 0
    cost += FIND_COST * (bool(args.find) + bool(args.find_missing))
    if args.batch and not args.batch_id:
        cost += len(args.batch)
    return cost


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--spend", action="store_true", help="allow the paid steps")
    p.add_argument("--verify-email", metavar="EMAIL", help="step 3 (1 credit)")
    p.add_argument(
        "--find", nargs=3, metavar=("FIRST", "LAST", "DOMAIN"), help="step 4 (3 credits)"
    )
    p.add_argument(
        "--find-missing",
        nargs=3,
        metavar=("FIRST", "LAST", "DOMAIN"),
        help="optional step 4b, a made-up person (up to 3 credits)",
    )
    p.add_argument("--batch", nargs="+", metavar="EMAIL", help="step 5, 1-3 emails (1 credit each)")
    p.add_argument("--batch-id", help="read an existing batch instead of submitting one")
    p.add_argument("--formats", default="csv", help="extra results formats (comma-separated)")
    p.add_argument(
        "--filters",
        default="valid,invalid,risky,unknown",
        help="extra results filters (comma-separated)",
    )
    p.add_argument("--poll-timeout", type=float, default=120, help="seconds to wait for a batch")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixture directory")
    args = p.parse_args(argv)
    args.formats = [f for f in args.formats.split(",") if f]
    args.filters = [f for f in args.filters.split(",") if f]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cost = planned_cost(args)
    if cost and not args.spend:
        print(f"Refusing: these inputs would spend {cost} credits. Add --spend.", file=sys.stderr)
        return 2
    if args.spend and not cost:
        print("--spend given but no paid inputs (--verify-email, --find, ...).", file=sys.stderr)
        return 2
    if args.batch and len(args.batch) > 3:
        print("--batch takes at most 3 emails.", file=sys.stderr)
        return 2

    load_dotenv()
    key = os.environ.get("MAILTESTER_API_KEY")
    if not key:
        print("MAILTESTER_API_KEY is not set. Add it to your environment or .env.", file=sys.stderr)
        return 2

    if cost:
        print(f"Paid steps will spend up to {cost} credits.")
    args.out.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    stopped = None
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=90) as client:
        rec = Recorder(client, build_redactor(args, key), args.out)
        rec.call("1", "credits_before", "GET", "/credits")
        rec.call("2", "lists", "GET", "/lists", params={"per_page": 5}, scrub_names=True)
        batch_id = args.batch_id
        try:
            if cost:
                batch_id = run_paid(rec, args) or batch_id
        except Stop as e:
            stopped = str(e)
        if batch_id and not stopped:
            run_batch_reads(rec, args, batch_id)
        if cost:
            rec.call("10", "credits_after", "GET", "/credits")
        run_errors(rec)

    print(f"\nWrote {len(rec.written)} files to {args.out}")
    status = 0
    if stopped:
        print(f"STOPPED: {stopped}", file=sys.stderr)
        status = 1
    leaks = rec.leaks()
    for name, hits in leaks.items():
        print(f"LEAK? {name}: {', '.join(hits)}", file=sys.stderr)
    if leaks:
        print("Review and fix these fixtures by hand before committing.", file=sys.stderr)
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())

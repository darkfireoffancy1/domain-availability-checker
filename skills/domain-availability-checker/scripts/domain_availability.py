#!/usr/bin/env python3
"""Batch domain registration and Cloudflare registrability checks."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


CONTRACT = "domain-availability-checker@1"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
IANA_RDAP_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
DEFAULT_OUTPUT_ROOT = Path.home() / "domain-availability-runs"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "domain-availability-checker" / "credentials.txt"
CF_BATCH_SIZE = 20
REQUIRED_RESULT_FIELDS = (
    "domain",
    "status",
    "registered_status",
    "cloudflare_registrable",
    "cloudflare_reason",
    "tier",
    "currency",
    "registration_cost",
    "renewal_cost",
    "sources",
    "checked_at",
    "price_checked_at",
    "provider_evidence",
    "conflicts",
    "notes",
)


class DomainInputError(ValueError):
    pass


class ProviderError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalize_domain(raw: str) -> str:
    value = raw.strip().lstrip("\ufeff")
    if not value:
        raise DomainInputError("empty domain")

    candidate = value
    if "://" in candidate:
        parsed = urllib.parse.urlsplit(candidate)
        candidate = parsed.hostname or ""
    else:
        candidate = candidate.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if "@" in candidate:
            candidate = candidate.rsplit("@", 1)[-1]
        if candidate.count(":") == 1:
            candidate = candidate.split(":", 1)[0]

    candidate = candidate.strip().rstrip(".").lower()
    if not candidate:
        raise DomainInputError(f"invalid domain: {raw!r}")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise DomainInputError(f"IP address is not a registrable domain: {raw!r}")

    try:
        ascii_domain = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainInputError(f"invalid IDN: {raw!r}") from exc

    if len(ascii_domain) > 253 or "." not in ascii_domain:
        raise DomainInputError(f"domain must contain a valid public suffix: {raw!r}")
    label_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if any(not label_pattern.fullmatch(label) for label in ascii_domain.split(".")):
        raise DomainInputError(f"invalid domain syntax: {raw!r}")
    return ascii_domain


def load_input_file(path: Path) -> list[str]:
    if not path.exists():
        raise DomainInputError(f"input file does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = payload.get("domains")
        if not isinstance(payload, list):
            raise DomainInputError("JSON input must be a list or an object with a domains list")
        values: list[str] = []
        for item in payload:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict) and isinstance(item.get("domain"), str):
                values.append(item["domain"])
            else:
                raise DomainInputError("each JSON item must be a string or an object with domain")
        return values
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise DomainInputError("CSV input must have a domain header")
            lookup = {name.strip().lower(): name for name in reader.fieldnames if name}
            column = lookup.get("domain")
            if not column:
                raise DomainInputError("CSV input must have a domain column")
            return [row[column] for row in reader if row.get(column, "").strip()]

    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def load_credentials(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    allowed = {"CLOUDFLARE_ACCOUNT_ID", "ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DomainInputError(f"invalid credentials line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed:
            raise DomainInputError(f"unsupported credentials key on line {line_number}: {key}")
        values[key] = value.strip().strip('"').strip("'")
    return values


def collect_domains(positional: Iterable[str], input_path: Path | None) -> tuple[list[str], list[dict[str, str]]]:
    raw_values = list(positional)
    if input_path:
        raw_values.extend(load_input_file(input_path))
    if not raw_values:
        raise DomainInputError("provide domains as arguments or with --input")

    domains: list[str] = []
    invalid: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_values:
        try:
            domain = normalize_domain(raw)
        except DomainInputError as exc:
            invalid.append({"input": raw, "error": str(exc)})
            continue
        if domain not in seen:
            seen.add(domain)
            domains.append(domain)
    if not domains:
        raise DomainInputError("no valid domains remained after normalization")
    return domains, invalid


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 20.0,
    attempts: int = 3,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json, application/rdap+json",
        "User-Agent": "domain-availability-checker/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw[:1000]}
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                return exc.code, payload
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
        time.sleep(1 + attempt)
    raise ProviderError(f"request failed after {attempts} attempts: {type(last_error).__name__}: {last_error}")


def empty_result(domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "status": "unknown",
        "registered_status": "unknown",
        "cloudflare_registrable": None,
        "cloudflare_reason": "",
        "tier": "",
        "currency": "",
        "registration_cost": "",
        "renewal_cost": "",
        "sources": [],
        "checked_at": utc_now(),
        "price_checked_at": "",
        "provider_evidence": {},
        "conflicts": [],
        "notes": [],
    }


def mark_conflict(row: dict[str, Any], message: str) -> None:
    row["conflicts"].append(message)
    row["notes"].append(message)
    row["status"] = "unknown"


def cloudflare_check(domains: list[str], account_id: str, token: str) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    endpoint = f"{CF_API_BASE}/accounts/{urllib.parse.quote(account_id)}/registrar/domain-check"
    public_endpoint = f"{CF_API_BASE}/accounts/{{account_id}}/registrar/domain-check"
    for batch in chunks(domains, CF_BATCH_SIZE):
        status, payload = request_json(endpoint, method="POST", body={"domains": batch}, token=token)
        if status != 200 or not payload.get("success"):
            errors = payload.get("errors") or []
            message = "; ".join(str(item.get("message", item)) for item in errors) or str(payload)[:500]
            raise ProviderError(f"Cloudflare domain-check HTTP {status}: {message}")
        returned = {
            item.get("name", "").lower(): item
            for item in (payload.get("result", {}).get("domains") or [])
            if isinstance(item, dict)
        }
        for domain in batch:
            row = empty_result(domain)
            row["sources"] = ["cloudflare"]
            item = returned.get(domain)
            if not item:
                row["notes"].append("Cloudflare omitted this domain from the response")
                row["provider_evidence"]["cloudflare"] = {
                    "raw_status": None, "normalized_status": "unknown", "reason": "omitted",
                    "source": public_endpoint, "tier": "", "currency": "",
                    "registration_cost": "", "renewal_cost": "",
                    "checked_at": row["checked_at"], "price_checked_at": "",
                }
                results[domain] = row
                continue
            registrable = item.get("registrable")
            reason = str(item.get("reason") or "")
            pricing = item.get("pricing") or {}
            checked_at = utc_now()
            registration_cost = str(pricing["registration_cost"]) if pricing.get("registration_cost") is not None else ""
            renewal_cost = str(pricing["renewal_cost"]) if pricing.get("renewal_cost") is not None else ""
            currency = str(pricing.get("currency") or "")
            row.update(
                {
                    "cloudflare_registrable": registrable if isinstance(registrable, bool) else None,
                    "cloudflare_reason": reason,
                    "tier": str(item.get("tier") or ""),
                    "currency": currency,
                    "registration_cost": registration_cost,
                    "renewal_cost": renewal_cost,
                    "checked_at": checked_at,
                    "price_checked_at": checked_at if registration_cost or renewal_cost else "",
                }
            )
            if registrable is True:
                row["status"] = "available_via_cloudflare"
                row["registered_status"] = "not_registered"
            elif reason == "domain_unavailable":
                row["status"] = "unavailable_or_reserved"
                row["registered_status"] = "registered_or_reserved"
            elif reason:
                row["status"] = "not_registrable_via_cloudflare"
            row["provider_evidence"]["cloudflare"] = {
                "raw_status": registrable if isinstance(registrable, bool) else None,
                "normalized_status": row["registered_status"],
                "reason": reason,
                "source": public_endpoint,
                "tier": row["tier"],
                "currency": currency,
                "registration_cost": registration_cost,
                "renewal_cost": renewal_cost,
                "checked_at": checked_at,
                "price_checked_at": row["price_checked_at"],
            }
            if (registration_cost or renewal_cost) and not currency:
                mark_conflict(row, "Cloudflare returned a price without a currency")
            if registrable is True and reason == "domain_unavailable":
                row["registered_status"] = "unknown"
                mark_conflict(row, "Cloudflare registrability and reason disagree")
            results[domain] = row
    return results


def load_rdap_bootstrap() -> dict[str, list[str]]:
    status, payload = request_json(IANA_RDAP_BOOTSTRAP)
    if status != 200:
        raise ProviderError(f"IANA RDAP bootstrap HTTP {status}")
    mapping: dict[str, list[str]] = {}
    for service in payload.get("services") or []:
        if not isinstance(service, list) or len(service) != 2:
            continue
        tlds, urls = service
        if not isinstance(tlds, list) or not isinstance(urls, list):
            continue
        for tld in tlds:
            mapping[str(tld).lower()] = [str(url) for url in urls]
    return mapping


def rdap_check(domains: list[str]) -> dict[str, dict[str, Any]]:
    mapping = load_rdap_bootstrap()
    results: dict[str, dict[str, Any]] = {}
    for domain in domains:
        row = empty_result(domain)
        row["sources"] = ["rdap"]
        tld = domain.rsplit(".", 1)[-1]
        servers = mapping.get(tld)
        if not servers:
            row["notes"].append(f"No IANA RDAP service listed for .{tld}")
            results[domain] = row
            continue
        url = servers[0].rstrip("/") + "/domain/" + urllib.parse.quote(domain)
        try:
            status, payload = request_json(url, attempts=2)
        except ProviderError as exc:
            row["notes"].append(str(exc))
            row["provider_evidence"]["rdap"] = {
                "raw_status": None, "normalized_status": "unknown", "reason": "request_failed",
                "source": url, "tier": "", "currency": "",
                "registration_cost": "", "renewal_cost": "",
                "checked_at": row["checked_at"], "price_checked_at": "",
            }
            results[domain] = row
            continue
        checked_at = utc_now()
        if status == 200 and str(payload.get("ldhName") or "").lower().rstrip(".") == domain:
            row["status"] = "registered"
            row["registered_status"] = "registered"
        elif status == 404:
            row["status"] = "not_registered"
            row["registered_status"] = "not_registered"
            row["notes"].append("RDAP not-found does not prove that a reserved name is purchasable")
        else:
            title = payload.get("title") or payload.get("errorCode") or "unknown response"
            row["notes"].append(f"RDAP HTTP {status}: {title}")
        row["checked_at"] = checked_at
        row["provider_evidence"]["rdap"] = {
            "raw_status": status,
            "normalized_status": row["registered_status"],
            "reason": str(payload.get("title") or payload.get("errorCode") or ""),
            "source": url,
            "tier": "",
            "currency": "",
            "registration_cost": "",
            "renewal_cost": "",
            "checked_at": checked_at,
            "price_checked_at": "",
        }
        results[domain] = row
    return results


def merge_rdap(base: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged["sources"] = list(dict.fromkeys([*base.get("sources", []), *fallback.get("sources", [])]))
    merged["notes"] = [*base.get("notes", []), *fallback.get("notes", [])]
    merged["provider_evidence"] = {**base.get("provider_evidence", {}), **fallback.get("provider_evidence", {})}
    merged["conflicts"] = [*base.get("conflicts", []), *fallback.get("conflicts", [])]
    merged["checked_at"] = max(base["checked_at"], fallback["checked_at"])
    if merged["conflicts"]:
        merged["status"] = "unknown"
        return merged
    if fallback["registered_status"] == "registered" and base.get("cloudflare_registrable") is True:
        merged["registered_status"] = "unknown"
        mark_conflict(merged, "Cloudflare says registrable, but RDAP reports a registered domain")
        return merged
    if fallback["registered_status"] != "unknown":
        merged["registered_status"] = fallback["registered_status"]
    if fallback["registered_status"] == "registered":
        merged["status"] = "registered"
    elif base.get("cloudflare_registrable") is True:
        merged["status"] = "available_via_cloudflare"
    elif fallback["registered_status"] == "not_registered" and base.get("cloudflare_registrable") is False:
        merged["status"] = "not_registered_but_not_registrable_via_cloudflare"
    elif fallback["status"] != "unknown":
        merged["status"] = fallback["status"]
    return merged


def run_checks(
    domains: list[str], provider: str, credentials_path: Path = DEFAULT_CREDENTIALS_PATH
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    file_credentials = load_credentials(credentials_path) if provider != "rdap" else {}
    account_id = (
        (os.getenv("CLOUDFLARE_ACCOUNT_ID") or os.getenv("ACCOUNT_ID")) if provider != "rdap" else None
    ) or (
        file_credentials.get("CLOUDFLARE_ACCOUNT_ID")
        or file_credentials.get("ACCOUNT_ID")
    )
    token = (os.getenv("CLOUDFLARE_API_TOKEN") if provider != "rdap" else None) or file_credentials.get("CLOUDFLARE_API_TOKEN")
    if bool(account_id) != bool(token):
        raise ProviderError(
            "Cloudflare configuration is incomplete: set both CLOUDFLARE_ACCOUNT_ID (or ACCOUNT_ID) "
            "and CLOUDFLARE_API_TOKEN"
        )

    metadata: dict[str, Any] = {
        "provider_requested": provider,
        "cloudflare_used": False,
        "rdap_used": False,
        "cloudflare_error": "",
        "credentials_source": "environment" if provider != "rdap" and os.getenv("CLOUDFLARE_API_TOKEN") else (
            "local_file" if token else "none"
        ),
    }
    if provider == "cloudflare" and not (account_id and token):
        raise ProviderError("--provider cloudflare requires Cloudflare account and token environment variables")

    by_domain: dict[str, dict[str, Any]] = {}
    use_cloudflare = provider == "cloudflare" or (provider == "auto" and bool(account_id and token))
    if use_cloudflare:
        try:
            by_domain = cloudflare_check(domains, account_id or "", token or "")
            metadata["cloudflare_used"] = True
        except ProviderError as exc:
            if provider == "cloudflare":
                raise
            metadata["cloudflare_error"] = str(exc)

    if provider == "rdap" or (provider == "auto" and not by_domain):
        by_domain = rdap_check(domains)
        metadata["rdap_used"] = True
    elif provider == "auto":
        fallback_domains = [
            domain
            for domain, row in by_domain.items()
            if row.get("registered_status") == "unknown"
            or row.get("cloudflare_reason")
            in {
                "extension_not_supported_via_api",
                "extension_not_supported",
                "extension_disallows_registration",
            }
        ]
        if fallback_domains:
            rdap_results = rdap_check(fallback_domains)
            metadata["rdap_used"] = True
            for domain in fallback_domains:
                by_domain[domain] = merge_rdap(by_domain[domain], rdap_results[domain])

    return [by_domain[domain] for domain in domains], metadata


def summarize(results: list[dict[str, Any]], invalid: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(results), "invalid_inputs": len(invalid)}
    for row in results:
        key = row["status"]
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_outputs(
    output_dir: Path,
    results: list[dict[str, Any]],
    invalid: list[dict[str, str]],
    metadata: dict[str, Any],
) -> tuple[Path, Path, dict[str, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = summarize(results, invalid)
    payload = {
        "skill_contract": CONTRACT,
        "generated_at": utc_now(),
        **metadata,
        "counts": counts,
        "invalid_inputs": invalid,
        "domains": results,
    }
    json_path = output_dir / "result.json"
    csv_path = output_dir / "result.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_RESULT_FIELDS)
        writer.writeheader()
        for row in results:
            csv_row = dict(row)
            csv_row["sources"] = ";".join(row["sources"])
            csv_row["provider_evidence"] = json.dumps(row["provider_evidence"], ensure_ascii=False, sort_keys=True)
            csv_row["conflicts"] = json.dumps(row["conflicts"], ensure_ascii=False)
            csv_row["notes"] = "; ".join(row["notes"])
            writer.writerow(csv_row)
    return json_path, csv_path, counts


def default_output_dir() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUTPUT_ROOT / stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-check domain registration status and Cloudflare registrability without purchasing domains."
    )
    parser.add_argument("domains", nargs="*", help="Domains or URLs to check")
    parser.add_argument("--input", type=Path, help="TXT, CSV (domain column), or JSON domain list")
    parser.add_argument("--provider", choices=("auto", "cloudflare", "rdap"), default="auto")
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help=f"Local KEY=VALUE credential file (default: {DEFAULT_CREDENTIALS_PATH})",
    )
    parser.add_argument("--output-dir", type=Path, help="Output directory; defaults under your home directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        domains, invalid = collect_domains(args.domains, args.input)
        results, metadata = run_checks(domains, args.provider, args.credentials_file)
        output_dir = args.output_dir or default_output_dir()
        json_path, csv_path, counts = write_outputs(output_dir, results, invalid, metadata)
    except (DomainInputError, ProviderError, OSError, json.JSONDecodeError) as exc:
        print(f"DOMAIN_AVAILABILITY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    has_unknown = bool(invalid) or any(row["status"] == "unknown" for row in results)
    marker = "DOMAIN_AVAILABILITY_PARTIAL" if has_unknown else "DOMAIN_AVAILABILITY_OK"
    print(marker)
    print(f"JSON_PATH: {json_path.resolve()}")
    print(f"CSV_PATH: {csv_path.resolve()}")
    print("COUNTS: " + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 2 if has_unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate domain-availability-checker JSON and CSV outputs."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path


CONTRACT = "domain-availability-checker@1"
REQUIRED_FIELDS = {
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
}
VALID_STATUS = {
    "available_via_cloudflare",
    "registered",
    "not_registered",
    "not_registered_but_not_registrable_via_cloudflare",
    "not_registrable_via_cloudflare",
    "unavailable_or_reserved",
    "unknown",
}
EVIDENCE_FIELDS = {
    "raw_status", "normalized_status", "reason", "source", "tier", "currency",
    "registration_cost", "renewal_cost", "checked_at", "price_checked_at",
}


def parse_time(value: object, label: str, errors: list[str]) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a timestamp with a timezone")
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        errors.append(f"{label} has an invalid timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{label} must include a timezone")
        return None
    return parsed


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read JSON: {exc}"]

    if payload.get("skill_contract") != CONTRACT:
        errors.append(f"skill_contract must be {CONTRACT}")
    generated_at = parse_time(payload.get("generated_at"), "generated_at", errors)
    domains = payload.get("domains")
    if not isinstance(domains, list):
        return [*errors, "domains must be a list"]

    seen: set[str] = set()
    for index, row in enumerate(domains):
        if not isinstance(row, dict):
            errors.append(f"domains[{index}] must be an object")
            continue
        missing = REQUIRED_FIELDS - row.keys()
        if missing:
            errors.append(f"domains[{index}] missing fields: {sorted(missing)}")
        domain = row.get("domain")
        if domain in seen:
            errors.append(f"duplicate domain: {domain}")
        seen.add(domain)
        if row.get("status") not in VALID_STATUS:
            errors.append(f"domains[{index}] has invalid status: {row.get('status')}")
        sources = row.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"domains[{index}] must include at least one source")
        if not isinstance(row.get("notes"), list):
            errors.append(f"domains[{index}].notes must be a list")
        prefix = f"domains[{index}]"
        checked_at = parse_time(row.get("checked_at"), f"{prefix}.checked_at", errors)
        if checked_at and generated_at and checked_at > generated_at:
            errors.append(f"{prefix}.checked_at is newer than generated_at")
        evidence = row.get("provider_evidence")
        if not isinstance(evidence, dict) or not evidence:
            errors.append(f"{prefix}.provider_evidence must contain provider records")
            continue
        if isinstance(sources, list) and set(sources) != set(evidence):
            errors.append(f"{prefix}.sources must match provider_evidence keys")
        evidence_times: list[dt.datetime] = []
        for provider, item in evidence.items():
            label = f"{prefix}.provider_evidence.{provider}"
            if provider not in {"cloudflare", "rdap"} or not isinstance(item, dict):
                errors.append(f"{label} is not a valid provider record")
                continue
            if EVIDENCE_FIELDS - item.keys():
                errors.append(f"{label} missing fields: {sorted(EVIDENCE_FIELDS - item.keys())}")
            timestamp = parse_time(item.get("checked_at"), f"{label}.checked_at", errors)
            if timestamp:
                evidence_times.append(timestamp)
                if generated_at and timestamp > generated_at:
                    errors.append(f"{label}.checked_at is newer than generated_at")
            if item.get("registration_cost") or item.get("renewal_cost"):
                if not item.get("currency"):
                    errors.append(f"{label} has a price without a currency")
                if item.get("price_checked_at") != item.get("checked_at"):
                    errors.append(f"{label} price/status timestamps differ")
            elif item.get("price_checked_at"):
                errors.append(f"{label} has a price timestamp without a price")
        if checked_at and evidence_times and checked_at != max(evidence_times):
            errors.append(f"{prefix}.checked_at must be the newest provider check time")
        cf = evidence.get("cloudflare")
        rdap = evidence.get("rdap")
        if isinstance(cf, dict):
            for field in ("currency", "tier", "registration_cost", "renewal_cost", "price_checked_at"):
                if row.get(field) != cf.get(field):
                    errors.append(f"{prefix}.{field} differs from Cloudflare evidence")
            if row.get("cloudflare_registrable") != cf.get("raw_status"):
                errors.append(f"{prefix}.cloudflare_registrable differs from Cloudflare evidence")
        if row.get("price_checked_at") and not isinstance(cf, dict):
            errors.append(f"{prefix} claims a price without Cloudflare evidence")
        conflicts = row.get("conflicts")
        if not isinstance(conflicts, list):
            errors.append(f"{prefix}.conflicts must be a list")
        elif conflicts and row.get("status") != "unknown":
            errors.append(f"{prefix} cannot claim a determinate status with conflicts")
        if isinstance(cf, dict) and isinstance(rdap, dict):
            if cf.get("raw_status") is True and rdap.get("normalized_status") == "registered":
                if not conflicts or row.get("status") != "unknown":
                    errors.append(f"{prefix} must retain the Cloudflare/RDAP disagreement")
        if row.get("status") == "available_via_cloudflare" and row.get("cloudflare_registrable") is not True:
            errors.append(f"{prefix} claims Cloudflare availability without confirmation")
        if row.get("status") == "not_registered" and row.get("cloudflare_registrable") is True:
            errors.append(f"{prefix} drops Cloudflare availability")
        if isinstance(rdap, dict) and rdap.get("raw_status") == 404 and row.get("status") == "available_via_cloudflare" and not isinstance(cf, dict):
            errors.append(f"{prefix} presents RDAP not-found as Cloudflare-purchasable")
        if isinstance(rdap, dict) and rdap.get("raw_status") == 404 and not isinstance(cf, dict):
            if row.get("registered_status") != "not_registered" or row.get("status") != "not_registered":
                errors.append(f"{prefix} RDAP not-found must remain not_registered")
            if not any("reserved" in str(note).lower() for note in (row.get("notes") if isinstance(row.get("notes"), list) else [])):
                errors.append(f"{prefix} RDAP not-found must retain the reserved-name limitation")

    counts = payload.get("counts") or {}
    if counts.get("total") != len(domains):
        errors.append("counts.total does not match domains length")

    csv_path = path.with_name("result.csv")
    if not csv_path.exists():
        errors.append(f"missing CSV sibling: {csv_path}")
    else:
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                csv_rows = list(reader)
                if set(reader.fieldnames or []) != REQUIRED_FIELDS:
                    errors.append("CSV columns do not match required result fields")
            if len(csv_rows) != len(domains):
                errors.append("CSV row count does not match JSON domains length")
            elif any(csv_row.get("domain") != row.get("domain") or csv_row.get("status") != row.get("status") for csv_row, row in zip(csv_rows, domains) if isinstance(row, dict)):
                errors.append("CSV domain/status rows do not match JSON")
        except OSError as exc:
            errors.append(f"cannot read CSV: {exc}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a domain availability result bundle")
    parser.add_argument("result_json", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.result_json)
    if errors:
        for error in errors:
            print(f"DOMAIN_AVAILABILITY_CHECK_ERROR: {error}", file=sys.stderr)
        return 1
    print(f"DOMAIN_AVAILABILITY_CHECK_OK: {args.result_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

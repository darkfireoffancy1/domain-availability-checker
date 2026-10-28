#!/usr/bin/env python3
"""Offline contract tests for domain-availability-checker."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("domain_availability", SCRIPT_DIR / "domain_availability.py")
assert SPEC and SPEC.loader
domain_availability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(domain_availability)

CHECK_SPEC = importlib.util.spec_from_file_location("check_output", SCRIPT_DIR / "check_output.py")
assert CHECK_SPEC and CHECK_SPEC.loader
check_output = importlib.util.module_from_spec(CHECK_SPEC)
CHECK_SPEC.loader.exec_module(check_output)


class DomainAvailabilityTests(unittest.TestCase):
    def offline_bundle(self, domain: str = "example.com"):
        row = domain_availability.empty_result(domain)
        row.update({"status": "registered", "registered_status": "registered", "sources": ["rdap"]})
        row["provider_evidence"]["rdap"] = {
            "raw_status": 200, "normalized_status": "registered", "reason": "",
            "source": "https://rdap.example/domain/" + domain,
            "tier": "", "currency": "", "registration_cost": "", "renewal_cost": "",
            "checked_at": row["checked_at"], "price_checked_at": "",
        }
        return row

    def check_row(self, row):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, _, _ = domain_availability.write_outputs(
                Path(temp_dir), [row], [], {"provider_requested": "rdap"}
            )
            return check_output.validate(json_path)

    def test_normalize_and_deduplicate(self) -> None:
        domains, invalid = domain_availability.collect_domains(
            ["HTTPS://Example.COM/path", "example.com.", "bad_domain"], None
        )
        self.assertEqual(domains, ["example.com"])
        self.assertEqual(len(invalid), 1)

    def test_cloudflare_batches_at_twenty(self) -> None:
        domains = [f"candidate-{index}.com" for index in range(21)]
        calls: list[list[str]] = []

        def fake_request(url: str, **kwargs):
            batch = kwargs["body"]["domains"]
            calls.append(batch)
            return 200, {
                "success": True,
                "result": {
                    "domains": [
                        {
                            "name": domain,
                            "registrable": True,
                            "tier": "standard",
                            "pricing": {
                                "currency": "USD",
                                "registration_cost": "9.00",
                                "renewal_cost": "9.00",
                            },
                        }
                        for domain in batch
                    ]
                },
            }

        with mock.patch.object(domain_availability, "request_json", side_effect=fake_request):
            results = domain_availability.cloudflare_check(domains, "account", "token")
        self.assertEqual([len(batch) for batch in calls], [20, 1])
        self.assertTrue(all(row["status"] == "available_via_cloudflare" for row in results.values()))

    def test_load_credentials_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "credentials.txt"
            path.write_text(
                "# local only\nCLOUDFLARE_ACCOUNT_ID=account-123\n"
                "CLOUDFLARE_API_TOKEN=token-456\n",
                encoding="utf-8",
            )
            values = domain_availability.load_credentials(path)
        self.assertEqual(values["CLOUDFLARE_ACCOUNT_ID"], "account-123")
        self.assertEqual(values["CLOUDFLARE_API_TOKEN"], "token-456")

    def test_rdap_registered_and_not_found(self) -> None:
        def fake_request(url: str, **kwargs):
            if url == domain_availability.IANA_RDAP_BOOTSTRAP:
                return 200, {"services": [[["com"], ["https://rdap.example/"]]]}
            if url.endswith("/domain/taken.com"):
                return 200, {"ldhName": "taken.com"}
            return 404, {"errorCode": 404}

        with mock.patch.object(domain_availability, "request_json", side_effect=fake_request):
            results = domain_availability.rdap_check(["taken.com", "open.com"])
        self.assertEqual(results["taken.com"]["registered_status"], "registered")
        self.assertEqual(results["open.com"]["registered_status"], "not_registered")
        self.assertIsNone(results["open.com"]["cloudflare_registrable"])

    def test_output_contract(self) -> None:
        result = self.offline_bundle()
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, csv_path, _ = domain_availability.write_outputs(
                Path(temp_dir), [result], [], {"provider_requested": "rdap"}
            )
            self.assertTrue(csv_path.exists())
            self.assertEqual(check_output.validate(json_path), [])
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["skill_contract"], domain_availability.CONTRACT)

    def test_conflicting_providers_keep_unknown_and_ledger(self) -> None:
        row = self.offline_bundle()
        cf = domain_availability.empty_result("example.com")
        cf.update({"status": "available_via_cloudflare", "registered_status": "not_registered",
                   "cloudflare_registrable": True, "sources": ["cloudflare"]})
        cf["provider_evidence"]["cloudflare"] = {
            "raw_status": True, "normalized_status": "not_registered", "reason": "",
            "source": "https://api.cloudflare.com/client/v4/accounts/{account_id}/registrar/domain-check",
            "tier": "", "currency": "", "registration_cost": "", "renewal_cost": "",
            "checked_at": cf["checked_at"], "price_checked_at": "",
        }
        merged = domain_availability.merge_rdap(cf, row)
        self.assertEqual(merged["status"], "unknown")
        self.assertEqual(merged["registered_status"], "unknown")
        self.assertTrue(merged["conflicts"])
        self.assertEqual(self.check_row(merged), [])
        corrupt = copy.deepcopy(merged)
        corrupt["status"] = "registered"
        self.assertTrue(self.check_row(corrupt))

    def test_price_requires_currency_and_matching_timestamp(self) -> None:
        row = self.offline_bundle()
        row["sources"] = ["cloudflare"]
        row["provider_evidence"] = {"cloudflare": {
            "raw_status": True, "normalized_status": "not_registered", "reason": "",
            "source": "https://api.cloudflare.com/client/v4/accounts/{account_id}/registrar/domain-check",
            "tier": "standard", "currency": "USD", "registration_cost": "9.00",
            "renewal_cost": "9.00", "checked_at": row["checked_at"],
            "price_checked_at": row["checked_at"],
        }}
        row.update({"status": "available_via_cloudflare", "registered_status": "not_registered",
                    "cloudflare_registrable": True, "tier": "standard", "currency": "USD",
                    "registration_cost": "9.00", "renewal_cost": "9.00",
                    "price_checked_at": row["checked_at"]})
        self.assertEqual(self.check_row(row), [])
        missing_currency = copy.deepcopy(row)
        missing_currency["currency"] = ""
        missing_currency["provider_evidence"]["cloudflare"]["currency"] = ""
        self.assertTrue(any("currency" in err for err in self.check_row(missing_currency)))
        mismatched_time = copy.deepcopy(row)
        mismatched_time["price_checked_at"] = "2020-01-01T00:00:00+00:00"
        mismatched_time["provider_evidence"]["cloudflare"]["price_checked_at"] = "2020-01-01T00:00:00+00:00"
        self.assertTrue(any("timestamps differ" in err for err in self.check_row(mismatched_time)))

    def test_stale_claim_and_reserved_name_are_rejected(self) -> None:
        row = self.offline_bundle("reserved.example")
        row["status"] = "not_registered"
        row["registered_status"] = "not_registered"
        row["provider_evidence"]["rdap"].update({"raw_status": 404, "normalized_status": "not_registered"})
        row["notes"].append("RDAP not-found does not prove that a reserved name is purchasable")
        self.assertEqual(self.check_row(row), [])
        no_caveat = copy.deepcopy(row)
        no_caveat["notes"] = []
        self.assertTrue(any("reserved-name" in err for err in self.check_row(no_caveat)))
        false_purchase = copy.deepcopy(row)
        false_purchase["status"] = "available_via_cloudflare"
        self.assertTrue(self.check_row(false_purchase))
        stale_claim = copy.deepcopy(row)
        stale_claim["checked_at"] = "2020-01-01T00:00:00+00:00"
        self.assertTrue(any("newest provider" in err for err in self.check_row(stale_claim)))

    def test_cloudflare_evidence_never_contains_account_id(self) -> None:
        def fake_request(url: str, **kwargs):
            return 200, {"success": True, "result": {"domains": [
                {"name": "example.com", "registrable": True, "pricing": {"currency": "USD", "registration_cost": "9.00"}}
            ]}}
        with mock.patch.object(domain_availability, "request_json", side_effect=fake_request):
            row = domain_availability.cloudflare_check(["example.com"], "private-account-id", "private-token")["example.com"]
        self.assertNotIn("private-account-id", json.dumps(row))
        self.assertNotIn("private-token", json.dumps(row))
        self.assertEqual(self.check_row(row), [])

    def test_cloudflare_missing_currency_returns_unknown(self) -> None:
        with mock.patch.object(domain_availability, "request_json", return_value=(200, {
            "success": True, "result": {"domains": [{
                "name": "example.com", "registrable": True,
                "pricing": {"registration_cost": "9.00"},
            }]},
        })):
            row = domain_availability.cloudflare_check(["example.com"], "account", "token")["example.com"]
        self.assertEqual(row["status"], "unknown")
        self.assertTrue(row["conflicts"])
        self.assertTrue(self.check_row(row))

    def test_rdap_ignores_unrelated_broken_credential_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "credentials.txt"
            path.write_text("not=a-skill-key\n", encoding="utf-8")
            with mock.patch.object(domain_availability, "rdap_check", return_value={
                "example.com": self.offline_bundle()
            }), mock.patch.dict(domain_availability.os.environ, {"CLOUDFLARE_API_TOKEN": "unpaired"}):
                results, metadata = domain_availability.run_checks(["example.com"], "rdap", path)
        self.assertEqual(results[0]["status"], "registered")
        self.assertEqual(metadata["credentials_source"], "none")

    def test_runtime_has_no_purchase_endpoint(self) -> None:
        source = (SCRIPT_DIR / "domain_availability.py").read_text(encoding="utf-8")
        self.assertNotIn("/registrar/registrations", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

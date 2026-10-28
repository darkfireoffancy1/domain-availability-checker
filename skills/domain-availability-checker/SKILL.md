---
name: domain-availability-checker
description: >-
  Batch-check whether domain names are registered and whether Cloudflare can register them now, including current registration and renewal pricing. Use for 批量查询域名、域名有没有被注册、域名是否可注册、查域名价格、Cloudflare 域名查询、域名候选筛选、domain availability, bulk domain check, registrar lookup, RDAP lookup, or screening TXT/CSV/JSON domain lists. Uses Cloudflare Registrar domain-check in batches of 20 when credentials are available and falls back to registry RDAP for registration status. Read-only: it never purchases, renews, or modifies a domain.
business_loop: "底座(项目命名) · 域名候选批量查询与价格对账自动化，减少逐个打开注册商搜索 · 反馈:离线测试覆盖来源冲突与数据校验"
lifecycle: active
verification_harness: hybrid
---

# Domain Availability Checker

<!-- default-scope: full-applicable@1 -->
Apply the full relevant workflow unless the user explicitly limits the request.

## Planning preflight

Before execution, inspect the request and local state. Keep the six fields compact, but do not make silent assumptions.

- `known_inputs`: confirmed domains or input file, intended provider, required output, and whether Cloudflare credentials are available.
- `missing_or_ambiguous`: unclear input column, malformed names, or whether the user means registry registration status versus purchasability through Cloudflare.
- `assumptions`: defaults such as `--provider auto`, duplicate removal, IDN conversion to punycode, and output under `~/domain-availability-runs/`.
- `blockers`: only missing information that changes security, cost, side effects, or output shape. A query-only run has no purchase side effect. Never ask for API secrets in chat.
- `execution_plan`: normalize and deduplicate inputs, query in bounded batches, preserve provider evidence, write JSON and CSV, then run the checker.
- `verification_plan`: run offline tests, validate the result bundle, and cite the output and checker evidence paths.

Resolve non-secret facts from available sources before asking the user. If Cloudflare credentials are absent, `auto` uses RDAP and records that Cloudflare pricing was not checked.

## Goal

Turn a domain list into a machine-readable, auditable result bundle without purchasing anything. Keep these two questions separate:

- `registered_status`: whether a registry RDAP object exists, or whether Cloudflare has confirmed a name can be registered.
- `cloudflare_registrable`: whether Cloudflare Registrar currently allows this account to purchase the name.

An RDAP 404 supports `registered_status=not_registered`; it does not prove that a reserved name is purchasable. Only Cloudflare `domain-check` can produce `available_via_cloudflare` and Cloudflare pricing.

## Canonical runtime

- CLI: `scripts/domain_availability.py` (relative to this `SKILL.md`)
- Output checker: `scripts/check_output.py`
- Offline tests: `scripts/test_domain_availability.py`
- Default output root: `~/domain-availability-runs/`
- Contract marker: `domain-availability-checker@1`

Run the canonical CLI directly. Do not reproduce its provider logic, status mapping, output schema, or token handling in an anonymous script.

## Authentication

Cloudflare mode reads credentials from environment variables first, then from the local credentials file:

- `CLOUDFLARE_ACCOUNT_ID`, with `ACCOUNT_ID` accepted as a fallback.
- `CLOUDFLARE_API_TOKEN`, requiring Cloudflare `Registrar Write` permission during the API beta.
- Default local file: `~/.config/domain-availability-checker/credentials.txt`.

The local file uses `KEY=VALUE` lines and remains outside the Git workspace. Never print or pass the token as a command-line argument. Environment variables override file values. `--provider cloudflare` fails closed when credentials are absent or incomplete. `--provider auto` uses Cloudflare when both values exist and otherwise uses RDAP.

## Invocation

From the directory containing this `SKILL.md`, check arguments directly:

```powershell
python scripts/domain_availability.py example.com example.dev --provider auto
```

Check a text file containing one domain per line:

```powershell
python scripts/domain_availability.py --input domains.txt --provider auto
```

CSV input must have a `domain` header. JSON input may be a string list, an object with a `domains` list, or a list of objects containing `domain`.

Provider behavior:

- `auto`: Cloudflare when configured; RDAP for unsupported extensions or as the credential-free provider.
- `cloudflare`: Cloudflare only; no RDAP fallback. The script submits at most 20 domains per API request.
- `rdap`: registry RDAP only; no Cloudflare price or account-specific registrability result.

## Output contract

Every successful or partial run writes:

- `result.json`: metadata, invalid inputs, counts, and full per-domain evidence.
- `result.csv`: one row per normalized domain for filtering and handoff.

Per-domain fields:

- `status`: `available_via_cloudflare`, `registered`, `not_registered`, `not_registered_but_not_registrable_via_cloudflare`, `not_registrable_via_cloudflare`, `unavailable_or_reserved`, or `unknown`.
- `registered_status`: `registered`, `not_registered`, `registered_or_reserved`, or `unknown`.
- `cloudflare_registrable`: boolean or null.
- `cloudflare_reason`: Cloudflare's reason, including `domain_unavailable` or `extension_not_supported_via_api`.
- `tier`, `currency`, `registration_cost`, `renewal_cost`: populated only from Cloudflare.
- `sources`, `checked_at`, `price_checked_at`, `provider_evidence`, `conflicts`, `notes`: provider-specific evidence, timestamps, disagreements, and limitations. Cloudflare account IDs are redacted from evidence URLs.

The CLI exits `0` and prints `DOMAIN_AVAILABILITY_OK` when all valid domains have a determinate result. Invalid inputs or unknown results produce outputs, print `DOMAIN_AVAILABILITY_PARTIAL`, and exit `2`. Provider or configuration failures print `DOMAIN_AVAILABILITY_ERROR` and exit `2`.

## Safety boundaries

- This Skill may call only Cloudflare `domain-check` and public RDAP endpoints.
- Never call `/registrar/registrations`, renewal, transfer, contact-update, or other write endpoints.
- DNS records and website responses are not registration evidence. Do not infer availability from NXDOMAIN or an empty website.
- Treat provider errors and unsupported extensions as unknown until another authoritative source resolves them.
- A displayed price is a point-in-time Cloudflare quote. Recheck it before any separately authorized purchase workflow.

## Acceptance criteria

- Every accepted input appears exactly once after normalization; rejected inputs remain in `invalid_inputs` with a reason.
- Cloudflare requests contain no more than 20 domains and use the official `domain-check` endpoint.
- Registration status and Cloudflare registrability remain separate in every output.
- JSON and CSV row counts agree, include `skill_contract=domain-availability-checker@1`, and expose source and check time.
- If providers disagree, retain both records and a conflict ledger, and report an unknown result rather than choosing one.
- No credential value appears in output or logs.
- No domain purchase, renewal, transfer, contact update, DNS mutation, or billing action occurs.

## Provider-Evidence Reconciliation

- Preserve each provider's raw status, normalized status, reason, source URL/endpoint (with account ID redacted), price fields, currency, and `checked_at` independently before deriving the final row.
- If RDAP, Cloudflare registrability, or pricing evidence conflicts, keep the conflict ledger and return `unknown` or the narrower supported statement; do not silently prioritize one source.
- State status and price freshness explicitly in the delivered result. When the user needs a purchase decision, recheck live rather than applying an undocumented maximum-age rule.
- A no-network or no-live-canary run may deliver only as offline/partial evidence and cannot claim current registrability or current price.
- Keep expected-fail fixtures for provider disagreement, stale evidence presented as current, reserved-name ambiguity, missing currency, and price/status timestamp mismatch.

## Verification Harness

Harness type: `hybrid`.

Run the deterministic checks:

```powershell
python scripts/test_domain_availability.py
python scripts/check_output.py <run-directory>/result.json
```

Any failed item blocks completion and external delivery.

- [ ] Every domain acceptance criterion in this Skill passes for the requested scope.
- [ ] Run `scripts/test_domain_availability.py` and `scripts/check_output.py`; both exit successfully, and the checker prints `DOMAIN_AVAILABILITY_CHECK_OK` for the delivered bundle.
- [ ] At least one known registered domain is classified correctly in a live canary when network access is available.
- [ ] Any `not_registered` RDAP result retains the reserved-name limitation and is not presented as Cloudflare-purchasable.
- [ ] Provider conflicts, freshness, and no-live-canary limitations pass `Provider-Evidence Reconciliation`.
- [ ] Every planning assumption is resolved, explicitly retained as a caveat, or listed as a blocker.
- [ ] The completion report cites the result bundle and checker evidence; file generation alone is not completion.

## Official sources

- Cloudflare Registrar API guide: `https://developers.cloudflare.com/registrar/registrar-api/`
- Cloudflare Registrar API reference: `https://developers.cloudflare.com/api/resources/registrar/`
- IANA RDAP bootstrap: `https://data.iana.org/rdap/dns.json`

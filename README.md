# Domain Availability Checker

Batch-check domain registration via registry RDAP and, with your own Cloudflare credentials, account-specific registrability and registration/renewal prices via Cloudflare Registrar. The tool only calls Cloudflare's `domain-check` endpoint and public RDAP services; it never purchases a domain.

## Requirements

- Python 3.10+; standard library only.
- Optional Cloudflare account ID and API token for Cloudflare checks. Cloudflare's Registrar API currently requires the `Registrar Write` permission even for `domain-check`.

## Run

From `skills/domain-availability-checker/`:

```sh
python scripts/domain_availability.py example.com example.dev --provider rdap
python scripts/domain_availability.py --input domains.txt --provider auto --output-dir ./results
python scripts/test_domain_availability.py
python scripts/check_output.py ./results/result.json
```

`domains.txt` is one name per line; CSV input needs a `domain` header. JSON accepts a string array, `{ "domains": [...] }`, or objects with a `domain` key. Output is `result.json` plus `result.csv`. Without `--output-dir`, runs go under `~/domain-availability-runs/`.

Set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in your environment, or put `KEY=VALUE` lines in `~/.config/domain-availability-checker/credentials.txt`. Keep credentials outside the repository and never paste them into an issue. `--provider cloudflare` requires both values; `auto` uses RDAP when they are absent.

RDAP `404` means no registration record was found; reserved names may still be unavailable. Cloudflare prices and availability are point-in-time values. Conflicting provider evidence produces `unknown` with a conflict ledger. An output file records the time of the checks; recheck live before making a purchase decision.

## Agent Skill

The reusable Skill is `skills/domain-availability-checker/SKILL.md`. Install that directory into your agent's skills folder, keeping its `scripts/` alongside `SKILL.md`. The `.claude-plugin/plugin.json` manifest supports Claude Code plugin discovery.

## License

MIT. See [LICENSE](LICENSE).

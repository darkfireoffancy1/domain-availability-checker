# Domain Availability Checker

Batch-check domain registration via registry RDAP and, with your own Cloudflare credentials, account-specific registrability and registration/renewal prices via Cloudflare Registrar. The tool only calls Cloudflare's `domain-check` endpoint and public RDAP services; it never purchases a domain.

## Requirements

- Python 3.10+; standard library only.
- Optional Cloudflare account ID and API token for Cloudflare checks. Cloudflare's Registrar API currently requires the `Registrar Write` permission even for `domain-check`.

## One-command agent installation

Run the command for your operating system in a terminal. It downloads only this Skill's four runtime files and installs them into **existing** Claude Code, Codex, Cursor and/or OpenCode agent directories. It leaves other Skills and Cloudflare credentials alone. Close and reopen the agent afterward if it does not immediately recognize the new Skill.

**Windows PowerShell** (Python Launcher `py -3`):

```powershell
py -3 -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/darkfireoffancy1/domain-availability-checker/main/install.py', timeout=20).read().decode('utf-8'))"
```

**macOS Terminal** (`python3`):

```sh
python3 -c 'import urllib.request; exec(urllib.request.urlopen("https://raw.githubusercontent.com/darkfireoffancy1/domain-availability-checker/main/install.py", timeout=20).read().decode("utf-8"))'
```

If no agent directory exists yet, clone the repository and specify the desired client explicitly, for example `python3 install.py --source . --agents codex` (on Windows use `py -3 install.py --source . --agents codex`). Available values: `claude,codex,cursor,opencode` or `all`. `--dry-run` previews changes; an existing customized Skill is left untouched unless you explicitly pass `--replace`. Re-running the same version is safe and reports zero changed files. Installing the Skill does not install Python or configure a Cloudflare account.

Default destinations under your home directory: `.claude/skills/`, `.codex/skills/`, `.cursor/skills/` (also `.cursor/skills-cursor/` if that legacy directory exists), and `.config/opencode/skills/`. OpenCode also respects `XDG_CONFIG_HOME` if set. Windows uses `C:\Users\<your-name>\` as the home directory; macOS uses `/Users/<your-name>/`. Both run the same Python query code.

## Run

From `skills/domain-availability-checker/`:

```sh
python scripts/domain_availability.py example.com example.dev --provider rdap
python scripts/domain_availability.py --input domains.txt --provider auto --output-dir ./results
python scripts/test_domain_availability.py
python scripts/check_output.py ./results/result.json
```

`domains.txt` is one name per line; CSV input needs a `domain` header. JSON accepts a string array, `{ "domains": [...] }`, or objects with a `domain` key. Output is `result.json` plus `result.csv`. Without `--output-dir`, runs go under `~/domain-availability-runs/`.

Set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in your environment, or put `KEY=VALUE` lines in `~/.config/domain-availability-checker/credentials.txt`. On Windows, `~` expands to your Windows user profile; on macOS, to your user home. Keep credentials outside the repository and never paste them into an issue. The installer does not request, read or transfer credentials. `--provider cloudflare` requires both values; `auto` uses RDAP when they are absent.

RDAP `404` means no registration record was found; reserved names may still be unavailable. Cloudflare prices and availability are point-in-time values. Conflicting provider evidence produces `unknown` with a conflict ledger. An output file records the time of the checks; recheck live before making a purchase decision.

## Agent Skill

The reusable Skill is `skills/domain-availability-checker/SKILL.md`. The installer keeps its `scripts/` alongside `SKILL.md`. The `.claude-plugin/plugin.json` manifest supports Claude Code plugin discovery. To check the installer offline from a clone, run `python3 test_install.py` (Windows: `py -3 test_install.py`).

## License

MIT. See [LICENSE](LICENSE).

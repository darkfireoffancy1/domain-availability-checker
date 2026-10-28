#!/usr/bin/env python3
"""Install this public Skill into existing agent directories on Windows or macOS."""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


REPO = "darkfireoffancy1/domain-availability-checker"
ARCHIVE = f"https://codeload.github.com/{REPO}/zip/refs/heads/main"
SKILL_NAME = "domain-availability-checker"
FILES = (
    "SKILL.md",
    "scripts/domain_availability.py",
    "scripts/check_output.py",
    "scripts/test_domain_availability.py",
)
AGENTS = ("claude", "codex", "cursor", "opencode")
MAX_ARCHIVE_BYTES = 2_000_000


def source_files(source: Path | None) -> dict[str, bytes]:
    prefix = f"skills/{SKILL_NAME}/"
    if source is not None:
        return {name: (source / prefix / name).read_bytes() for name in FILES}
    request = urllib.request.Request(ARCHIVE, headers={"User-Agent": "domain-availability-checker-installer"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("GitHub archive exceeds installer size limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = {name: f"{SKILL_NAME}-main/{prefix}{name}" for name in FILES}
        if any(archive.getinfo(entry).file_size > MAX_ARCHIVE_BYTES for entry in entries.values()):
            raise ValueError("Skill file exceeds installer size limit")
        return {name: archive.read(entry) for name, entry in entries.items()}


def targets(home: Path, agents: str, xdg_config: str | None = None) -> list[Path]:
    selected = set(AGENTS) if agents == "all" else set(AGENTS) if agents == "auto" else set(agents.split(","))
    if not selected or selected - set(AGENTS):
        raise ValueError("--agents must be auto, all, or comma-separated claude,codex,cursor,opencode")
    config = Path(xdg_config).expanduser() if xdg_config else home / ".config"
    roots = {
        "claude": home / ".claude",
        "codex": home / ".codex",
        "cursor": home / ".cursor",
        "opencode": config / "opencode",
    }
    if agents == "auto":
        selected = {agent for agent in selected if roots[agent].is_dir()}
        if not selected:
            raise ValueError("No existing agent directory detected; rerun with --agents claude (or your client)")
    result = [roots[agent] / "skills" / SKILL_NAME for agent in AGENTS if agent in selected]
    legacy_cursor = roots["cursor"] / "skills-cursor"
    if "cursor" in selected and legacy_cursor.is_dir():
        result.append(legacy_cursor / SKILL_NAME)
    return result


def install(payload: dict[str, bytes], destinations: list[Path], *, dry_run: bool, replace: bool) -> list[str]:
    if not payload["SKILL.md"].startswith(b"---\nname: domain-availability-checker\n"):
        raise ValueError("Downloaded Skill name does not match the requested Skill")
    changes: list[tuple[Path, bytes]] = []
    results: list[str] = []
    for folder in destinations:
        for name, content in payload.items():
            destination = folder / name
            if destination.is_symlink():
                raise ValueError(f"Refusing to follow a symlink: {destination}")
            if destination.exists() and destination.read_bytes() == content:
                continue
            if destination.exists() and not replace:
                raise ValueError(f"Existing modified Skill file: {destination}; review it before using --replace")
            changes.append((destination, content))
        results.append(str(folder))
    if not dry_run:
        for destination, content in changes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".skill-install-", delete=False) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            try:
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
    print(f"INSTALL_{'DRY_RUN' if dry_run else 'OK'}: {len(destinations)} agent locations, {len(changes)} files {'would change' if dry_run else 'changed'}")
    for folder in results:
        print(f"SKILL_PATH: {folder}")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the domain-availability-checker Skill without copying credentials")
    parser.add_argument("--agents", default="auto", help="auto, all, or claude,codex,cursor,opencode")
    parser.add_argument("--source", type=Path, help="Use an already cloned repository instead of downloading it")
    parser.add_argument("--home", type=Path, help="Override the user home directory (mainly for isolated tests)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Replace existing modified Skill files after review")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    try:
        home = args.home.expanduser().resolve() if args.home else Path.home()
        xdg = None if args.home else os.environ.get("XDG_CONFIG_HOME")
        destinations = targets(home, args.agents, xdg)
        payload = source_files(args.source)
        install(payload, destinations, dry_run=args.dry_run, replace=args.replace)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"INSTALL_ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

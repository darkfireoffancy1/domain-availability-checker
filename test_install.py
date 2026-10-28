"""Offline Windows/macOS-home installation checks; never writes real agent folders."""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import install


ROOT = Path(__file__).resolve().parent


class InstallTests(unittest.TestCase):
    def test_auto_detects_existing_agent_roots_and_cursor_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for path in (".claude", ".cursor/skills-cursor", ".config/opencode"):
                (home / path).mkdir(parents=True)
            found = install.targets(home, "auto")
            self.assertEqual(found, [
                home / ".claude/skills/domain-availability-checker",
                home / ".cursor/skills/domain-availability-checker",
                home / ".config/opencode/skills/domain-availability-checker",
                home / ".cursor/skills-cursor/domain-availability-checker",
            ])
            self.assertFalse((home / ".codex").exists())

    def test_mac_style_home_and_xdg(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "Users" / "alice"
            found = install.targets(home, "codex,opencode", str(home / "Library" / "xdg"))
            self.assertEqual(found, [
                home / ".codex/skills/domain-availability-checker",
                home / "Library/xdg/opencode/skills/domain-availability-checker",
            ])

    def test_dry_run_install_idempotency_and_preserved_credentials(self):
        payload = install.source_files(ROOT)
        self.assertEqual(set(payload), set(install.FILES))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            destinations = install.targets(home, "all")
            credentials = home / ".config/domain-availability-checker/credentials.txt"
            credentials.parent.mkdir(parents=True)
            credentials.write_text("CLOUDFLARE_API_TOKEN=private-test-value\n", encoding="utf-8")
            install.install(payload, destinations, dry_run=True, replace=False)
            self.assertFalse(destinations[0].exists())
            install.install(payload, destinations, dry_run=False, replace=False)
            for destination in destinations:
                for name, content in payload.items():
                    self.assertEqual((destination / name).read_bytes(), content)
            self.assertEqual(credentials.read_text(encoding="utf-8"), "CLOUDFLARE_API_TOKEN=private-test-value\n")
            install.install(payload, destinations, dry_run=False, replace=False)

    def test_conflict_fails_before_any_other_agent_is_modified(self):
        payload = install.source_files(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            destinations = install.targets(Path(directory), "claude,codex")
            existing = destinations[1] / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user customization", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Existing modified Skill"):
                install.install(payload, destinations, dry_run=False, replace=False)
            self.assertFalse(destinations[0].exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "user customization")

    def test_archive_reads_only_allowlisted_files(self):
        payload = install.source_files(ROOT)
        archive_bytes = io.BytesIO()
        prefix = "domain-availability-checker-main/skills/domain-availability-checker/"
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            for name, content in payload.items():
                archive.writestr(prefix + name, content)
            archive.writestr("domain-availability-checker-main/credentials.txt", "sensitive")
        with mock.patch.object(install.urllib.request, "urlopen", return_value=io.BytesIO(archive_bytes.getvalue())):
            self.assertEqual(install.source_files(None), payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)

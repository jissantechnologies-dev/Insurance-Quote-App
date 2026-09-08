"""Checks on the deploy script and the preflight report.

deploy.sh cannot be run here - it targets the cPanel host - so these tests
assert the properties that make it safe to run unattended. The excludes test is
the important one: everything gitignored under InsuranceQuoteApp/ is live
server data, and a deploy that copies over it destroys logins, chat history or
customer documents.

Run with:  py test_deploy.py
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import preflight

BASE_DIR = Path(__file__).resolve().parent
DEPLOY_SH = BASE_DIR / "deploy.sh"
GITIGNORE = BASE_DIR.parent / ".gitignore"


def working_bash():
        """A bash that can actually run. On Windows the first bash on PATH may
        be the WSL stub, which fails to exec when WSL has no distro."""
        for candidate in ("bash", r"C:\Program Files\Git\bin\bash.exe"):
                try:
                        probe = subprocess.run(
                                [candidate, "-c", "echo ok"],
                                capture_output=True, text=True, timeout=20,
                        )
                except (OSError, subprocess.SubprocessError):
                        continue
                if probe.returncode == 0 and probe.stdout.strip() == "ok":
                        return candidate
        return None


BASH = working_bash()


class DeployScript(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
                cls.text = DEPLOY_SH.read_text(encoding="utf-8")
                # The prose explains what the script avoids doing, so assertions
                # about what it *does* have to ignore comments.
                cls.code = "\n".join(
                        line for line in cls.text.splitlines()
                        if not line.lstrip().startswith("#")
                )

        @unittest.skipUnless(BASH, "no working bash on this machine")
        def test_is_valid_bash(self):
                result = subprocess.run(
                        [BASH, "-n", str(DEPLOY_SH)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

        def test_aborts_on_any_error(self):
                self.assertIn("set -euo pipefail", self.code)

        def test_rsync_never_deletes(self):
                """--delete would remove anything in the app root that is not in
                the repo, which is precisely the live data."""
                self.assertNotIn("--delete ", self.code)
                self.assertFalse(
                        re.search(r"--delete\b(?!-)", self.code),
                        "deploy.sh passes --delete to rsync",
                )

        def test_excludes_every_gitignored_app_file(self):
                ignored = []
                for line in GITIGNORE.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                                continue
                        if line.startswith("InsuranceQuoteApp/"):
                                ignored.append(line.split("/", 1)[1].rstrip("/"))

                self.assertTrue(ignored, "no app-scoped entries found in .gitignore")
                for name in ignored:
                        self.assertRegex(
                                self.text,
                                rf'--exclude "{re.escape(name)}/?"',
                                f"deploy.sh would overwrite live data: {name}",
                        )

        def test_pull_is_fast_forward_only(self):
                """A diverged branch means someone committed on the server."""
                self.assertIn("--ff-only", self.code)

        def test_refuses_to_run_with_a_dirty_checkout(self):
                self.assertIn("git status --porcelain", self.code)

        def test_preflight_runs_before_the_restart(self):
                """A failing preflight must leave the old code serving."""
                self.assertLess(
                        self.text.index("preflight.py"),
                        self.text.index("tmp/restart.txt"),
                )

        def test_backs_up_htaccess_before_editing_it(self):
                self.assertLess(
                        self.text.index(".htaccess.bak"),
                        self.text.index("mv \"$HTACCESS.new\""),
                )


class PreflightReport(unittest.TestCase):
        def test_counts_and_renders(self):
                report = preflight.Report()
                report.add(preflight.OK, "one")
                report.add(preflight.WARN, "two", "detail")
                report.add(preflight.FAIL, "three")
                self.assertEqual(
                        (report.counts(preflight.OK), report.counts(preflight.WARN),
                         report.counts(preflight.FAIL)),
                        (1, 1, 1),
                )
                rendered = report.render()
                self.assertIn("[WARN] two", rendered)
                self.assertIn("1 ok, 1 warning(s), 1 failure(s)", rendered)

        def test_this_machine_has_no_failures(self):
                """The dev machine has every hard requirement installed, so any
                FAIL here is a real breakage rather than an environment gap."""
                report = preflight.run()
                failures = [row for row in report.rows if row[0] == preflight.FAIL]
                self.assertEqual(failures, [], f"preflight failures: {failures}")

        def test_strict_turns_warnings_into_a_nonzero_exit(self):
                """The unset WhatsApp vars on a dev box are warnings, so --strict
                is expected to come back 2 here and 0 on a configured server."""
                self.assertEqual(preflight.main([]), 0)
                self.assertEqual(preflight.main(["--strict"]), 2)


class HtaccessCheck(unittest.TestCase):
        """The July 2026 outage: without the override every route but / is a 500."""

        def check_with(self, contents):
                report = preflight.Report()
                original = preflight.BASE_DIR
                with tempfile.TemporaryDirectory() as directory:
                        preflight.BASE_DIR = Path(directory)
                        if contents is not None:
                                (preflight.BASE_DIR / ".htaccess").write_text(
                                        contents, encoding="utf-8"
                                )
                        try:
                                preflight.check_htaccess(report)
                        finally:
                                preflight.BASE_DIR = original
                return report.rows[0]

        def test_missing_override_is_a_failure(self):
                status, _, detail = self.check_with(
                        "PassengerAppRoot /home/user/app\nPassengerAppType wsgi\n"
                )
                self.assertEqual(status, preflight.FAIL)
                self.assertIn("500", detail)

        def test_present_override_passes(self):
                status, _, _ = self.check_with(
                        "RewriteEngine On\nRewriteRule ^ - [L]\n\nPassengerAppRoot /x\n"
                )
                self.assertEqual(status, preflight.OK)

        def test_partial_override_is_still_a_failure(self):
                """RewriteEngine On alone does not stop the inherited rule."""
                status, _, _ = self.check_with("RewriteEngine On\nPassengerAppRoot /x\n")
                self.assertEqual(status, preflight.FAIL)

        def test_no_htaccess_is_fine_locally(self):
                status, _, _ = self.check_with(None)
                self.assertEqual(status, preflight.OK)


if __name__ == "__main__":
        unittest.main()

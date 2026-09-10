"""Checks on the dev/production split.

The failure these guard against is a quiet one: an environment that looks
fine but writes to the other one's data. Every assertion here is about
keeping the two data sets apart.

Run with:  py test_environments.py
"""

import contextlib
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import paths

BASE_DIR = Path(__file__).resolve().parent
GITIGNORE = BASE_DIR.parent / ".gitignore"
DEPLOY_SH = BASE_DIR / "deploy.sh"
ARCHIVE_SH = BASE_DIR / "archive_data.sh"

# Modules that resolve a live-data path at import time. Each is re-imported
# under the temporary settings to prove it follows them.
DATA_MODULES = ("auth", "chat", "reminders", "main")


def _reload_data_modules():
        importlib.reload(paths)
        return {name: importlib.reload(importlib.import_module(name))
                for name in DATA_MODULES}


@contextlib.contextmanager
def env_vars(**overrides):
        """Run a block under different GI_* settings, then put everything back.

        The modules resolve their paths at import time, so they are reloaded
        on the way in and again on the way out - otherwise a test would leave
        the rest of the suite pointed at a deleted temporary directory.
        """
        previous = {key: os.environ.get(key) for key in overrides}
        for key, value in overrides.items():
                if value is None:
                        os.environ.pop(key, None)
                else:
                        os.environ[key] = str(value)
        try:
                yield _reload_data_modules()
        finally:
                for key, value in previous.items():
                        if value is None:
                                os.environ.pop(key, None)
                        else:
                                os.environ[key] = value
                _reload_data_modules()


class DataDirectory(unittest.TestCase):
        def test_defaults_to_the_app_directory(self):
                """An unconfigured checkout must keep working unchanged."""
                with env_vars(GI_DATA_DIR=None):
                        self.assertEqual(paths.resolve_data_dir(), paths.BASE_DIR)

        def test_every_live_data_path_follows_the_setting(self):
                with tempfile.TemporaryDirectory() as tmp:
                        tmp_path = Path(tmp).resolve()
                        with env_vars(GI_DATA_DIR=tmp_path) as modules:
                                checked = [
                                        modules["auth"].USERS_JSON_PATH,
                                        modules["auth"].SECRET_KEY_PATH,
                                        modules["chat"].DB_PATH,
                                        modules["reminders"].DB_PATH,
                                        modules["main"].CUSTOMERS_JSON_PATH,
                                        modules["main"].NEW_CUSTOMERS_TEXT_PATH,
                                        modules["main"].DOCUMENTS_DIR,
                                        modules["main"].CHAT_MEDIA_DIR,
                                ]
                                checked.extend(
                                        modules["main"].SEND_LINK_SENT_PATHS.values())
                                checked.extend(
                                        modules["main"].SEND_LINK_BATCH_PATHS.values())
                                for path in checked:
                                        self.assertEqual(
                                                Path(path).parent, tmp_path,
                                                f"{path} ignores GI_DATA_DIR",
                                        )

        def test_code_assets_stay_with_the_checkout(self):
                """Templates ship with the code and must not move per environment."""
                with tempfile.TemporaryDirectory() as tmp:
                        with env_vars(GI_DATA_DIR=tmp) as modules:
                                for path in (modules["main"].TEMPLATE_PATH,
                                             modules["main"].STYLE_PATH):
                                        self.assertEqual(
                                                Path(path).parent, paths.BASE_DIR)

        def test_a_blank_environment_has_no_customers(self):
                """Production starts empty, so load_customers cannot raise."""
                with tempfile.TemporaryDirectory() as tmp:
                        with env_vars(GI_DATA_DIR=tmp) as modules:
                                self.assertEqual(modules["main"].load_customers(), [])

        def test_two_environments_do_not_share_a_file(self):
                with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        with env_vars(GI_DATA_DIR=root / "dev") as modules:
                                dev_customers = modules["main"].CUSTOMERS_JSON_PATH
                        with env_vars(GI_DATA_DIR=root / "prod") as modules:
                                prod_customers = modules["main"].CUSTOMERS_JSON_PATH
                        self.assertNotEqual(dev_customers, prod_customers)


class EnvironmentLabel(unittest.TestCase):
        def test_unset_means_production(self):
                with env_vars(GI_ENV_NAME=None):
                        self.assertEqual(paths.ENV_NAME, "production")
                        self.assertTrue(paths.IS_PRODUCTION)

        def test_dev_is_not_production(self):
                with env_vars(GI_ENV_NAME="dev"):
                        self.assertFalse(paths.IS_PRODUCTION)

        def test_banner_shows_on_dev_and_not_on_production(self):
                with env_vars(GI_ENV_NAME="dev") as modules:
                        self.assertIn("DEV ENVIRONMENT",
                                      modules["main"].build_env_banner())
                with env_vars(GI_ENV_NAME=None) as modules:
                        self.assertEqual(modules["main"].build_env_banner(), "")

        def test_the_template_renders_the_banner_placeholder(self):
                """A stale placeholder would print the raw tag on every page."""
                import main as core

                html = (BASE_DIR / "template.html").read_text(encoding="utf-8")
                self.assertIn("{{ENV_BANNER}}", html)
                self.assertNotIn("{{ENV_BANNER}}",
                                 core.render_with_template("<p>x</p>"))


class CustomerDataIsLiveData(unittest.TestCase):
        """customers.json used to be tracked and rsynced, which meant the repo
        overwrote the server's client list on every deploy. Production cannot
        stay blank unless it is treated as live data everywhere."""

        LIVE_FILES = ("customers.json", "newcustomer.txt", "customerxlfile.xlsx")

        def test_not_tracked_by_git(self):
                result = subprocess.run(
                        ["git", "ls-files", "--"] + list(self.LIVE_FILES),
                        cwd=BASE_DIR, capture_output=True, text=True,
                )
                self.assertEqual(result.stdout.strip(), "",
                                 "customer data is still tracked in git")

        def test_gitignored(self):
                text = GITIGNORE.read_text(encoding="utf-8")
                for name in self.LIVE_FILES:
                        self.assertIn(f"InsuranceQuoteApp/{name}", text)

        def test_deploy_excludes_them(self):
                text = DEPLOY_SH.read_text(encoding="utf-8")
                for name in self.LIVE_FILES:
                        self.assertIn(f'--exclude "{name}"', text)

        def test_seed_copies_exist_for_dev(self):
                for name in self.LIVE_FILES:
                        self.assertTrue(
                                (BASE_DIR / "seed" / name).is_file(),
                                f"seed/{name} is missing - dev has nothing to start from",
                        )


def working_bash():
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


class Scripts(unittest.TestCase):
        @unittest.skipUnless(BASH, "no working bash on this machine")
        def test_scripts_are_valid_bash(self):
                for script in (DEPLOY_SH, ARCHIVE_SH):
                        result = subprocess.run(
                                [BASH, "-n", str(script)],
                                capture_output=True, text=True,
                        )
                        self.assertEqual(result.returncode, 0,
                                         f"{script.name}: {result.stderr}")

        @unittest.skipUnless(BASH, "no working bash on this machine")
        def test_deploy_rejects_an_unknown_environment(self):
                result = subprocess.run(
                        [BASH, str(DEPLOY_SH), "--env", "staging"],
                        capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 64)
                self.assertIn("Unknown --env", result.stderr)

        def test_archive_only_deletes_behind_an_explicit_flag(self):
                text = ARCHIVE_SH.read_text(encoding="utf-8")
                code = "\n".join(line for line in text.splitlines()
                                 if not line.lstrip().startswith("#"))
                self.assertIn('if [ "$BLANK" = 0 ]', code)
                # The removal must sit after the archive has been verified.
                self.assertLess(code.index("tar -tzf"), code.index("rm -rf"))

        def test_bootstrap_refuses_to_seed_the_app_directory(self):
                """Seeding with GI_DATA_DIR unset would put dev data in the repo."""
                env = dict(os.environ)
                env.pop("GI_DATA_DIR", None)
                result = subprocess.run(
                        [sys.executable, str(BASE_DIR / "bootstrap_data.py"), "--seed"],
                        cwd=BASE_DIR, capture_output=True, text=True, env=env,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("GI_DATA_DIR is unset", result.stdout + result.stderr)

        def test_bootstrap_seeds_a_fresh_dev_directory(self):
                with tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / "dev"
                        env = dict(os.environ)
                        env["GI_DATA_DIR"] = str(target)
                        env["GI_ENV_NAME"] = "dev"
                        result = subprocess.run(
                                [sys.executable,
                                 str(BASE_DIR / "bootstrap_data.py"), "--seed"],
                                cwd=BASE_DIR, capture_output=True, text=True, env=env,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue((target / "customers.json").is_file())

        def test_bootstrap_leaves_production_empty(self):
                with tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / "prod"
                        env = dict(os.environ)
                        env["GI_DATA_DIR"] = str(target)
                        result = subprocess.run(
                                [sys.executable, str(BASE_DIR / "bootstrap_data.py")],
                                cwd=BASE_DIR, capture_output=True, text=True, env=env,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
        unittest.main(verbosity=2)

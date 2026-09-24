from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api import config as api_config


class ApiConfigKeyAliasTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        api_config.load_api_settings.cache_clear()

    def load_settings_with_env(self, env: dict[str, str]) -> api_config.ApiSettings:
        api_config.load_api_settings.cache_clear()
        with patch.dict(os.environ, env, clear=True), patch.object(api_config, "_load_dotenv_files", lambda: None):
            return api_config.load_api_settings()

    def test_the_odds_api_key_accepts_lowercase_local_alias(self):
        settings = self.load_settings_with_env({"the_odds_api_key": "fixture-odds-key"})

        self.assertEqual("fixture-odds-key", settings.the_odds_api_key)

    def test_the_odds_api_key_accepts_short_local_alias(self):
        settings = self.load_settings_with_env({"odds_api_key": "fixture-short-odds-key"})

        self.assertEqual("fixture-short-odds-key", settings.the_odds_api_key)

    def test_the_odds_api_key_accepts_second_free_key_alias(self):
        settings = self.load_settings_with_env({"odds_api_key2": "fixture-second-free-key"})

        self.assertEqual("fixture-second-free-key", settings.the_odds_api_key)

    def test_the_odds_api_key_accepts_canonical_second_key_alias(self):
        settings = self.load_settings_with_env({"POLYDATA_THE_ODDS_API_KEY2": "fixture-canonical-second-key"})

        self.assertEqual("fixture-canonical-second-key", settings.the_odds_api_key)

    def test_canonical_the_odds_api_key_overrides_aliases(self):
        settings = self.load_settings_with_env(
            {
                "POLYDATA_THE_ODDS_API_KEY": "canonical-odds-key",
                "the_odds_api_key": "lowercase-odds-key",
                "odds_api_key": "short-odds-key",
                "THE_ODDS_API_KEY": "legacy-odds-key",
            }
        )

        self.assertEqual("canonical-odds-key", settings.the_odds_api_key)

    def test_second_free_key_overrides_exhausted_primary_key_when_present(self):
        settings = self.load_settings_with_env(
            {
                "POLYDATA_THE_ODDS_API_KEY": "exhausted-primary-key",
                "odds_api_key2": "fresh-second-free-key",
            }
        )

        self.assertEqual("fresh-second-free-key", settings.the_odds_api_key)


if __name__ == "__main__":
    unittest.main()

import unittest

from utils.script_config import normalize_script_config_name


class ScriptConfigNormalizationTests(unittest.TestCase):
    def test_adds_yml_suffix_when_missing(self):
        self.assertEqual(normalize_script_config_name("my_config"), "my_config.yml")

    def test_keeps_yml_suffix(self):
        self.assertEqual(normalize_script_config_name("my_config.yml"), "my_config.yml")

    def test_none_returns_none(self):
        self.assertIsNone(normalize_script_config_name(None))

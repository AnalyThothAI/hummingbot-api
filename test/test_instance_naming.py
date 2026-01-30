import unittest
from datetime import datetime
from unittest import mock

from utils import instance_naming


class InstanceNamingTests(unittest.TestCase):
    def test_unique_name_adds_timestamp_and_suffix(self):
        fixed_time = datetime(2025, 1, 2, 3, 4, 5)
        with mock.patch("utils.instance_naming.datetime") as mock_datetime:
            with mock.patch("utils.instance_naming.secrets.token_hex", return_value="beef"):
                mock_datetime.now.return_value = fixed_time
                instance_name, script_config, generated = instance_naming.build_controller_instance_name(
                    "My Bot",
                    unique=True,
                )

        self.assertEqual(instance_name, "My-Bot-20250102-030405-beef")
        self.assertEqual(script_config, "My-Bot-20250102-030405-beef.yml")
        self.assertTrue(generated)

    def test_unique_name_preserves_existing_suffix(self):
        instance_name, script_config, generated = instance_naming.build_controller_instance_name(
            "bot-20250102-030405-abcd",
            unique=True,
        )

        self.assertEqual(instance_name, "bot-20250102-030405-abcd")
        self.assertEqual(script_config, "bot-20250102-030405-abcd.yml")
        self.assertFalse(generated)

    def test_unique_name_adds_suffix_when_missing(self):
        with mock.patch("utils.instance_naming.secrets.token_hex", return_value="abcd"):
            instance_name, script_config, generated = instance_naming.build_controller_instance_name(
                "bot-20250102-030405",
                unique=True,
            )

        self.assertEqual(instance_name, "bot-20250102-030405-abcd")
        self.assertEqual(script_config, "bot-20250102-030405-abcd.yml")
        self.assertTrue(generated)

    def test_unique_disabled_uses_sanitized_name(self):
        instance_name, script_config, generated = instance_naming.build_controller_instance_name(
            "  !! Bot  ",
            unique=False,
        )

        self.assertEqual(instance_name, "Bot")
        self.assertEqual(script_config, "Bot.yml")
        self.assertFalse(generated)

    def test_should_generate_unique_name(self):
        self.assertTrue(instance_naming.should_generate_unique_name("bot", True))
        self.assertFalse(instance_naming.should_generate_unique_name("bot-20250102-030405-abcd", True))
        self.assertFalse(instance_naming.should_generate_unique_name("bot", False))

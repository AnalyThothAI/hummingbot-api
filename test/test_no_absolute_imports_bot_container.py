import re
import unittest
from pathlib import Path


class NoAbsoluteImportsForBotContainerTests(unittest.TestCase):
    """
    Bot containers mount these directories as top-level packages:
      - /home/hummingbot/controllers  -> import controllers.*
      - /home/hummingbot/scripts      -> import scripts.*

    Therefore, code inside bots/controllers and bots/scripts must NOT use absolute imports
    that assume a `bots.*` or `controllers.*` package root, otherwise the bot strategy may
    fail at runtime with `ModuleNotFoundError: No module named 'bots'`.
    """

    def test_no_bots_or_controllers_absolute_imports(self):
        repo_root = Path(__file__).resolve().parents[1]
        scan_roots = [
            repo_root / "bots" / "controllers",
            repo_root / "bots" / "scripts",
        ]

        import_re = re.compile(r"^\s*(from|import)\s+(bots|controllers)\b", re.MULTILINE)

        violations = []
        for root in scan_roots:
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="replace")
                match = import_re.search(text)
                if match:
                    # Report the first offending line for quick fixes.
                    line_no = text[: match.start()].count("\n") + 1
                    bad_line = text.splitlines()[line_no - 1].strip()
                    violations.append(f"{path.relative_to(repo_root)}:{line_no}: {bad_line}")

        self.assertFalse(
            violations,
            "Found forbidden absolute imports in bot-mounted code:\n" + "\n".join(violations),
        )


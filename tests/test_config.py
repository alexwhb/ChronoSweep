import tempfile
import unittest
from pathlib import Path

from folder_cleaner import build_service_from_config, load_config


class ConfigTests(unittest.TestCase):
    def test_loads_rules_and_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"
            config_path.write_text(
                """
{
  "trash_dir": "TRASH",
  "notifications": [{"type": "console"}],
  "rules": [
    {
      "path": "A",
      "retention_time": "5d",
      "notify_before": [2, 5],
      "action": "trash",
      "patterns": [
        {"pattern": "^ScreenShot", "retention_time": "1d", "action": "delete"}
      ]
    },
    {"path": "B", "retention_time": "12h", "notify_days_before": 1}
  ]
}
"""
            )

            cfg = load_config(config_path)
            self.assertEqual(2, len(cfg.rules))
            self.assertEqual("trash", cfg.rules[0].action)
            self.assertEqual({2, 5}, {int(td.total_seconds() // 86400) for td in cfg.rules[0].notify_before})
            self.assertEqual({1}, {int(td.total_seconds() // 86400) for td in cfg.rules[1].notify_before})
            self.assertEqual(5, cfg.rules[0].retention.days)
            self.assertEqual(12 * 3600, cfg.rules[1].retention.total_seconds())
            self.assertEqual(1, len(cfg.rules[0].patterns))
            self.assertEqual("delete", cfg.rules[0].patterns[0].action)
            self.assertEqual(Path("TRASH").expanduser().resolve(), cfg.trash_dir)
            self.assertEqual(1, len(cfg.notifications))

            service = build_service_from_config(cfg)
            self.assertEqual(cfg.trash_dir, service.trash_dir)


if __name__ == "__main__":
    unittest.main()

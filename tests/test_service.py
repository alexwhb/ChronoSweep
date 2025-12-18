import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from folder_cleaner import Alert, FolderCleanerService, FolderRule
from folder_cleaner.service import MacOSNotificationOutlet, PatternRule


class CollectorOutlet:
    name = "collector"

    def __init__(self) -> None:
        self.messages = []

    def send(self, alerts_by_date) -> None:
        self.messages.append(alerts_by_date)


def _touch_with_age(path: Path, *, base_date: date, days_ago: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data")
    target_date = base_date - timedelta(days=days_ago)
    timestamp = datetime.combine(target_date, datetime.min.time()).timestamp()
    os.utime(path, (timestamp, timestamp))


def _touch_with_offset(path: Path, *, base_dt: datetime, delta: timedelta) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data")
    target_dt = base_dt - delta
    os.utime(path, (target_dt.timestamp(), target_dt.timestamp()))


class FolderCleanerServiceTests(unittest.TestCase):
    def test_alerts_grouped_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_date = date(2024, 1, 10)
            folder_one = (Path(tmp_dir) / "folder_one").resolve()
            folder_two = (Path(tmp_dir) / "folder_two").resolve()

            # Configure modification times so both files alert on base_date.
            _touch_with_age(folder_one / "old.txt", base_date=base_date, days_ago=7)
            _touch_with_age(folder_two / "doc.txt", base_date=base_date, days_ago=15)

            service = FolderCleanerService(
                [
                    FolderRule(folder_one, retention=timedelta(days=10), notify_before=["3d"]),
                    FolderRule(folder_two, retention=timedelta(days=20), notify_before=["5d"]),
                ]
            )

            alerts = service.upcoming_alerts(as_of=base_date)
            self.assertIn(base_date, alerts)
            self.assertEqual(2, len(alerts[base_date]))

            alert_index = {alert.folder: alert for alert in alerts[base_date]}
            self.assertSetEqual({Path("old.txt")}, set(alert_index[folder_one].files))
            self.assertSetEqual({Path("doc.txt")}, set(alert_index[folder_two].files))
            self.assertEqual(3, alert_index[folder_one].days_until_deletion)
            self.assertEqual(5, alert_index[folder_two].days_until_deletion)

    def test_cleanup_respects_retention_and_exemptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_date = date(2024, 1, 10)
            root = Path(tmp_dir) / "retained"

            stale_file = root / "stale.txt"
            fresh_file = root / "fresh.txt"
            exempt_file = root / "keep.log"
            stale_dir = root / "old_dir"
            stale_dir_file = stale_dir / "nested.txt"
            exempt_dir = root / "keep_dir"
            exempt_dir_file = exempt_dir / "child.txt"

            _touch_with_age(stale_file, base_date=base_date, days_ago=12)
            _touch_with_age(fresh_file, base_date=base_date, days_ago=5)
            _touch_with_age(exempt_file, base_date=base_date, days_ago=20)
            _touch_with_age(stale_dir_file, base_date=base_date, days_ago=15)
            _touch_with_age(exempt_dir_file, base_date=base_date, days_ago=30)

            stale_dir_timestamp = datetime.combine(base_date - timedelta(days=15), datetime.min.time()).timestamp()
            exempt_dir_timestamp = datetime.combine(base_date - timedelta(days=30), datetime.min.time()).timestamp()
            os.utime(stale_dir, (stale_dir_timestamp, stale_dir_timestamp))
            os.utime(exempt_dir, (exempt_dir_timestamp, exempt_dir_timestamp))

            service = FolderCleanerService(
                [FolderRule(root, retention=timedelta(days=10), exemptions={"keep.log", "keep_dir"})]
            )

            removed = service.cleanup(as_of=base_date)
            self.assertIn(stale_file.resolve(), [path.resolve() for path in removed])
            self.assertFalse(stale_file.exists())
            self.assertTrue(fresh_file.exists())
            self.assertTrue(exempt_file.exists())
            self.assertFalse(stale_dir.exists())
            self.assertTrue(exempt_dir.exists())

    def test_trash_action_moves_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_date = date(2024, 2, 1)
            root = Path(tmp_dir) / "folder"
            trash_dir = Path(tmp_dir) / "trash"

            stale = root / "remove.txt"
            _touch_with_age(stale, base_date=base_date, days_ago=30)

            service = FolderCleanerService(
                [FolderRule(root, retention=timedelta(days=10), action="trash")],
                trash_dir=trash_dir,
            )

            removed = service.cleanup(as_of=base_date)
            self.assertFalse(stale.exists())
            self.assertTrue(trash_dir.exists())
            self.assertEqual(1, len(removed))
            self.assertTrue(removed[0].is_relative_to(service.trash_dir))

    def test_system_trash_moves_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_date = date(2024, 2, 1)
            root = Path(tmp_dir) / "folder"
            override_trash = Path(tmp_dir) / "mac_trash"

            stale = root / "remove.txt"
            _touch_with_age(stale, base_date=base_date, days_ago=30)

            os.environ["FOLDERCLEANER_SYSTEM_TRASH_OVERRIDE"] = str(override_trash)
            try:
                service = FolderCleanerService(
                    [FolderRule(root, retention=timedelta(days=10), action="system_trash")],
                )

                removed = service.cleanup(as_of=base_date)
                self.assertFalse(stale.exists())
                self.assertTrue(override_trash.exists())
                self.assertEqual(1, len(removed))
                self.assertTrue(removed[0].is_relative_to(override_trash))
            finally:
                os.environ.pop("FOLDERCLEANER_SYSTEM_TRASH_OVERRIDE", None)

    def test_notifications_sent_via_outlet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_date = date(2024, 3, 1)
            root = Path(tmp_dir) / "root"
            _touch_with_age(root / "soon.txt", base_date=base_date, days_ago=5)

            outlet = CollectorOutlet()
            service = FolderCleanerService(
                [FolderRule(root, retention=timedelta(days=10), notify_before=["2d"])],
                outlets=[outlet],
            )

            service.send_notifications(as_of=base_date, window_days=5)
            self.assertEqual(1, len(outlet.messages))
            alerts = outlet.messages[0]
            self.assertTrue(alerts)

    def test_multiple_notify_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_date = date(2024, 4, 10)
            root = Path(tmp_dir) / "root"
            _touch_with_age(root / "file.txt", base_date=base_date, days_ago=0)

            service = FolderCleanerService(
                [FolderRule(root, retention=timedelta(days=10), notify_before=["2d", "5d"])],
            )

            alerts = service.upcoming_alerts(as_of=base_date, window_days=10)
            expected_dates = {base_date + timedelta(days=5), base_date + timedelta(days=8)}
            self.assertSetEqual(set(alerts.keys()), expected_dates)

    def test_retention_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            now = datetime(2024, 5, 1, 12, 0, 0)
            root = Path(tmp_dir) / "hours"
            target = root / "hourly.txt"
            _touch_with_offset(target, base_dt=now, delta=timedelta(hours=7))

            service = FolderCleanerService([FolderRule(root, retention=timedelta(hours=6))])
            removed = service.cleanup(as_of=now)
            self.assertIn(target.resolve(), [p.resolve() for p in removed])
            self.assertFalse(target.exists())

    def test_pattern_specific_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = datetime(2024, 6, 1, 12, 0, 0)
            root = Path(tmp_dir) / "patterns"
            generic = root / "generic.txt"
            screenshot = root / "ScreenShot_001.png"

            _touch_with_offset(generic, base_dt=base, delta=timedelta(days=9))
            _touch_with_offset(screenshot, base_dt=base, delta=timedelta(days=2))

            rule = FolderRule(
                root,
                retention=timedelta(days=10),
                patterns=[PatternRule(pattern=r"^ScreenShot", retention=timedelta(days=1))],
            )
            service = FolderCleanerService([rule])

            removed = service.cleanup(as_of=base)
            self.assertIn(screenshot.resolve(), [p.resolve() for p in removed])
            self.assertNotIn(generic.resolve(), [p.resolve() for p in removed])
            self.assertTrue(generic.exists())

    def test_hour_level_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dt = datetime(2024, 7, 1, 0, 0, 0)
            root = Path(tmp_dir) / "hours_notify"
            _touch_with_offset(root / "file.txt", base_dt=base_dt, delta=timedelta(hours=0))

            service = FolderCleanerService(
                [FolderRule(root, retention=timedelta(hours=10), notify_before=["5h"])],
            )

            alerts = service.upcoming_alerts(as_of=base_dt, window_days=0)
        self.assertIn(base_dt.date(), alerts)

    def test_macos_outlet_formats_commands(self) -> None:
        commands: list[list[str]] = []

        def runner(cmd):
            commands.append(cmd)

        outlet = MacOSNotificationOutlet(
            title="TestTitle",
            subtitle="TestSub",
            sound="Glass",
            command_runner=runner,
            require_darwin=False,
        )

        alerts_by_date = {
            date(2024, 8, 1): [
                Alert(
                    folder=Path("/tmp/folder"),
                    files=[Path("a.txt"), Path("b.txt")],
                    alert_date=date(2024, 8, 1),
                    days_until_deletion=2,
                )
            ]
        }

        outlet.send(alerts_by_date)
        self.assertTrue(commands)
        self.assertEqual("osascript", commands[0][0])
        script = commands[0][2]
        self.assertIn("TestTitle", script)
        self.assertIn("Glass", script)
        self.assertIn("2 items", script)

if __name__ == "__main__":
    unittest.main()

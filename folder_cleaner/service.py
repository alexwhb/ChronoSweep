from __future__ import annotations

import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class PatternRule:
    pattern: str
    retention: timedelta
    notify_before: Iterable[timedelta] = field(default_factory=lambda: (timedelta(0),))
    action: str = "delete"

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern", self.pattern.strip())
        retention_value = self.retention
        if isinstance(retention_value, (int, float)):
            retention_value = timedelta(days=float(retention_value))
        if retention_value.total_seconds() < 0:
            raise ValueError("retention must be >= 0")
        object.__setattr__(self, "retention", retention_value)

        offsets = [_parse_offset(value) for value in self.notify_before]
        object.__setattr__(self, "notify_before", tuple(sorted(set(offsets), key=lambda td: td.total_seconds())))

        if self.action not in {"delete", "trash", "system_trash"}:
            raise ValueError("action must be 'delete', 'trash', or 'system_trash'")


@dataclass(frozen=True)
class FolderRule:
    path: Path
    retention: timedelta
    notify_before: Iterable[timedelta] = field(default_factory=lambda: (timedelta(0),))
    exemptions: Iterable[str] = field(default_factory=tuple)
    action: str = "delete"  # "delete" or "trash" or "system_trash"
    patterns: Iterable[PatternRule] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        resolved_path = Path(self.path).expanduser().resolve()
        object.__setattr__(self, "path", resolved_path)

        retention_value = self.retention
        if isinstance(retention_value, (int, float)):
            retention_value = timedelta(days=float(retention_value))
        if retention_value.total_seconds() < 0:
            raise ValueError("retention must be >= 0")
        object.__setattr__(self, "retention", retention_value)

        offsets = [_parse_offset(value) for value in self.notify_before]
        object.__setattr__(self, "notify_before", tuple(sorted(set(offsets), key=lambda td: td.total_seconds())))
        if self.action not in {"delete", "trash", "system_trash"}:
            raise ValueError("action must be 'delete', 'trash', or 'system_trash'")

        cleaned_exemptions = {ex.strip() for ex in self.exemptions if str(ex).strip()}
        object.__setattr__(self, "exemptions", cleaned_exemptions)


@dataclass(frozen=True)
class Alert:
    folder: Path
    files: List[Path]
    alert_date: date
    days_until_deletion: int


class NotificationOutlet(Protocol):
    name: str

    def send(self, alerts_by_date: Dict[date, List[Alert]]) -> None: ...


class ConsoleNotificationOutlet:
    name = "console"

    def send(self, alerts_by_date: Dict[date, List[Alert]]) -> None:
        for alert_date, alerts in sorted(alerts_by_date.items()):
            print(f"[FolderCleaner] Alerts for {alert_date.isoformat()}:")
            for alert in alerts:
                files_display = ", ".join(str(path) for path in alert.files)
                print(
                    f"  {alert.folder}: {files_display} (in {alert.days_until_deletion} days)"
                )


class MacOSNotificationOutlet:
    """
    Sends alerts as macOS Notification Center banners via osascript.

    Safe for tests by injecting command_runner and disabling platform checks.
    """

    name = "macos"

    def __init__(
        self,
        *,
        title: str = "ChronoSweep",
        subtitle: str | None = None,
        sound: str | None = None,
        command_runner: Optional[Callable[[List[str]], None]] = None,
        require_darwin: bool = True,
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        self.sound = sound
        self._run = command_runner or self._default_run
        self.require_darwin = require_darwin

    def send(self, alerts_by_date: Dict[date, List[Alert]]) -> None:
        import platform

        if self.require_darwin and platform.system() != "Darwin":
            return

        for alert_date, alerts in sorted(alerts_by_date.items()):
            for alert in alerts:
                files_display = ", ".join(str(path) for path in alert.files)
                suffix = "today" if alert.days_until_deletion == 0 else f"in {alert.days_until_deletion} days"
                message = f"{alert.folder}: {files_display} ({suffix})"

                notification_subtitle = self.subtitle or f"Due {alert_date.isoformat()}"
                script = self._build_script(message, notification_subtitle)
                self._run(["osascript", "-e", script])

    def _build_script(self, message: str, subtitle: str) -> str:
        parts = [
            f'display notification "{self._escape(message)}" with title "{self._escape(self.title)}"',
            f'subtitle "{self._escape(subtitle)}"',
        ]
        if self.sound:
            parts.append(f'sound name "{self._escape(self.sound)}"')
        return " ".join(parts)

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _default_run(cmd: List[str]) -> None:
        import subprocess

        subprocess.run(cmd, check=False)


class FolderCleanerService:
    def __init__(
        self,
        rules: Iterable[FolderRule],
        *,
        trash_dir: Optional[Path] = None,
        outlets: Optional[Iterable[NotificationOutlet]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.rules = list(rules)
        self.trash_dir = Path(trash_dir).expanduser().resolve() if trash_dir else None
        self.outlets = list(outlets or [])
        self._clock = clock or datetime.now

    def cleanup(self, as_of: Optional[date | datetime] = None) -> List[Path]:
        """
        Delete files and directories that are past their retention period.

        Returns a list of absolute paths that were removed.
        """
        as_of_dt = self._normalize_datetime(as_of)
        removed: List[Path] = []

        for rule in self.rules:
            if not rule.path.exists() or not rule.path.is_dir():
                continue

            entries = self._collect_entries(rule)
            # Remove deeper items first to avoid orphaning paths above them.
            entries.sort(key=lambda item: len(item[1].parts), reverse=True)

            for absolute_path, relative_path, modified_date in entries:
                if self._is_exempt(rule, relative_path):
                    continue
                effective_retention, action = self._effective_policy(rule, relative_path)
                age = as_of_dt - modified_date
                if age >= effective_retention:
                    if action == "trash":
                        trashed = self._move_to_trash(rule, absolute_path, relative_path)
                        removed.append(trashed)
                    elif action == "system_trash":
                        trashed = self._move_to_system_trash(rule, absolute_path, relative_path)
                        removed.append(trashed)
                    else:
                        self._remove_path(absolute_path)
                        removed.append(absolute_path)

        return removed

    def upcoming_alerts(
        self, *, as_of: Optional[date | datetime] = None, window_days: int = 0
    ) -> Dict[date, List[Alert]]:
        """
        Group alerts by the date they should be raised.

        window_days controls how far into the future to look (inclusive).
        A window of 0 returns alerts that should fire on the given date.
        """
        as_of_dt = self._normalize_datetime(as_of)
        as_of_date = as_of_dt.date()
        end_date = as_of_date + timedelta(days=window_days)

        schedule: Dict[date, Dict[Path, List[Tuple[Path, date]]]] = {}

        for rule in self.rules:
            if not rule.path.exists() or not rule.path.is_dir():
                continue

            for absolute_path, relative_path in self._iter_rule_entries(rule, topdown=True):
                if self._is_exempt(rule, relative_path):
                    continue

                modified_dt = self._modified_datetime(absolute_path)
                effective_retention, _ = self._effective_policy(rule, relative_path)
                due_dt = modified_dt + effective_retention
                if due_dt < as_of_dt:
                    continue

                notify_offsets = self._effective_notify(rule, relative_path)
                for notify_offset in notify_offsets:
                    alert_date = (due_dt - notify_offset).date()
                    if alert_date < as_of_date or alert_date > end_date:
                        continue
                    folder_alerts = schedule.setdefault(alert_date, {}).setdefault(rule.path, [])
                    folder_alerts.append((relative_path, due_dt.date()))

        grouped_alerts: Dict[date, List[Alert]] = {}
        for alert_date, folders in schedule.items():
            alerts: List[Alert] = []
            for folder_path, entries in folders.items():
                sorted_entries = sorted(entries, key=lambda item: item[0].as_posix())
                days_until_deletion = min(
                    math.ceil(
                        (datetime.combine(due, datetime.min.time()) - as_of_dt).total_seconds()
                        / 86400
                    )
                    for _, due in sorted_entries
                )
                alerts.append(
                    Alert(
                        folder=folder_path,
                        files=[path for path, _ in sorted_entries],
                        alert_date=alert_date,
                        days_until_deletion=days_until_deletion,
                    )
                )
            grouped_alerts[alert_date] = alerts

        return grouped_alerts

    def send_notifications(self, *, as_of: Optional[date | datetime] = None, window_days: int = 0) -> None:
        alerts = self.upcoming_alerts(as_of=as_of, window_days=window_days)
        for outlet in self.outlets:
            outlet.send(alerts)

    def _iter_rule_entries(
        self, rule: FolderRule, *, topdown: bool
    ) -> Iterable[Tuple[Path, Path]]:
        for root, dirs, files in os.walk(rule.path, topdown=topdown):
            root_path = Path(root)
            for name in files:
                absolute_path = root_path / name
                yield absolute_path, absolute_path.relative_to(rule.path)
            for name in dirs:
                absolute_path = root_path / name
                yield absolute_path, absolute_path.relative_to(rule.path)

    def _collect_entries(self, rule: FolderRule) -> List[Tuple[Path, Path, datetime]]:
        entries: List[Tuple[Path, Path, datetime]] = []
        for absolute_path, relative_path in self._iter_rule_entries(rule, topdown=True):
            entries.append((absolute_path, relative_path, self._modified_datetime(absolute_path)))
        return entries

    @staticmethod
    def _modified_datetime(path: Path) -> datetime:
        return datetime.fromtimestamp(path.stat().st_mtime)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _move_to_trash(self, rule: FolderRule, absolute_path: Path, relative_path: Path) -> Path:
        if not self.trash_dir:
            # If trash unavailable, fall back to deletion.
            self._remove_path(absolute_path)
            return absolute_path

        target = self.trash_dir / rule.path.name / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            target = target.with_name(f"{target.name}__{timestamp}")

        shutil.move(str(absolute_path), str(target))
        return target

    def _move_to_system_trash(self, rule: FolderRule, absolute_path: Path, relative_path: Path) -> Path:
        override = os.getenv("FOLDERCLEANER_SYSTEM_TRASH_OVERRIDE")
        base_trash = Path(override).expanduser() if override else Path.home() / ".Trash"
        base_trash.mkdir(parents=True, exist_ok=True)

        target = base_trash / rule.path.name / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.move(str(absolute_path), str(target))
            return target
        except Exception:
            # Fallback to simple delete if system trash move fails.
            self._remove_path(absolute_path)
            return absolute_path

    def _effective_policy(self, rule: FolderRule, relative_path: Path) -> Tuple[timedelta, str]:
        for pattern_rule in rule.patterns:
            if re.match(pattern_rule.pattern, relative_path.name):
                return pattern_rule.retention, pattern_rule.action
        return rule.retention, rule.action

    def _effective_notify(self, rule: FolderRule, relative_path: Path) -> Tuple[timedelta, ...]:
        for pattern_rule in rule.patterns:
            if re.match(pattern_rule.pattern, relative_path.name):
                return pattern_rule.notify_before
        return rule.notify_before

    @staticmethod
    def _is_exempt(rule: FolderRule, relative_path: Path) -> bool:
        if not rule.exemptions:
            return False

        rel_parts = relative_path.parts

        for exemption in rule.exemptions:
            ex_path = Path(exemption)

            if ex_path.is_absolute():
                if relative_path == ex_path:
                    return True
                continue

            ex_parts = ex_path.parts
            if len(ex_parts) <= len(rel_parts) and rel_parts[: len(ex_parts)] == ex_parts:
                return True
            if ex_path.name and ex_path.name == relative_path.name:
                return True

        return False

    @staticmethod
    def _normalize_datetime(value: Optional[date | datetime]) -> datetime:
        if value is None:
            return datetime.now()
        if isinstance(value, datetime):
            return value
        return datetime.combine(value, datetime.min.time())


def _parse_offset(value) -> timedelta:
    if isinstance(value, timedelta):
        if value.total_seconds() < 0:
            raise ValueError("notify_before values must be >= 0")
        return value
    if isinstance(value, bool):
        raise ValueError("notify_before values must be >= 0")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("notify_before values must be >= 0")
        return timedelta(days=float(value))
    if not isinstance(value, str):
        raise ValueError("notify_before must be numbers, timedeltas, or strings like '5h', '2d'")

    text = value.strip().lower()
    match = None
    for pattern in (r"^(\d+)([hdwy])$", r"^(\d+)$"):
        match = re.match(pattern, text)
        if match:
            break
    if not match:
        raise ValueError("notify_before string must look like '5d', '12h', or '1y'")

    amount = float(match.group(1))
    unit = match.group(2) if len(match.groups()) > 1 else "d"

    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    if unit == "y":
        return timedelta(days=amount * 365)
    raise ValueError("Unsupported notify_before unit; use h, d, w, or y")


class _ExemptionHelper:
    pass

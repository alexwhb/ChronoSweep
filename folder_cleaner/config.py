from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .service import (
    ConsoleNotificationOutlet,
    FolderCleanerService,
    FolderRule,
    MacOSNotificationOutlet,
    NotificationOutlet,
    PatternRule,
)

DEFAULT_TRASH_DIR = Path("~/.folder_cleaner_trash").expanduser()


@dataclass
class NotificationConfig:
    type: str
    options: Dict[str, str] = field(default_factory=dict)


@dataclass
class CleanerConfig:
    rules: List[FolderRule]
    trash_dir: Path = DEFAULT_TRASH_DIR
    notifications: List[NotificationConfig] = field(default_factory=list)


def load_config(path: Path) -> CleanerConfig:
    """
    Load configuration from a YAML (JSON-compatible) file.

    We prefer PyYAML if installed; otherwise we fall back to json.loads which
    supports a YAML subset. Keep configuration simple (mappings, lists, scalars)
    to remain compatible.
    """
    text = Path(path).read_text()
    data = _load_yaml_or_json(text)

    trash_dir = Path(data.get("trash_dir", DEFAULT_TRASH_DIR)).expanduser().resolve()
    notifications = [
        NotificationConfig(type=item.get("type", "console"), options=item.get("options", {}))
        for item in data.get("notifications", [])
    ]

    rules_data = data.get("rules", [])
    rules: List[FolderRule] = []
    for item in rules_data:
        notify_before = item.get("notify_before")
        if notify_before is None and "notify_days_before" in item:
            notify_before = [item["notify_days_before"]]
        if notify_before is None:
            notify_before = [0]
        if isinstance(notify_before, (int, str)):
            notify_before = [notify_before]
        notify_before = [_parse_duration(v) for v in notify_before]

        patterns = []
        for pat in item.get("patterns", []):
            notify_before_pat = pat.get("notify_before", notify_before)
            if isinstance(notify_before_pat, (int, str)):
                notify_before_pat = [notify_before_pat]
            notify_before_pat = [_parse_duration(v) for v in notify_before_pat]
            patterns.append(
                PatternRule(
                    pattern=pat["pattern"],
                    retention=_parse_duration(pat.get("retention_time") or pat.get("retention")),
                    notify_before=notify_before_pat,
                    action=pat.get("action", item.get("action", "delete")),
                )
            )

        rules.append(
            FolderRule(
                path=Path(item["path"]),
                retention=_parse_retention(item),
                notify_before=notify_before,
                exemptions=item.get("exemptions", []),
                action=item.get("action", "delete"),
                patterns=patterns,
            )
        )

    return CleanerConfig(rules=rules, trash_dir=trash_dir, notifications=notifications)


def build_service_from_config(
    config: CleanerConfig,
    *,
    outlet_factory: Optional[Callable[[NotificationConfig], NotificationOutlet]] = None,
) -> FolderCleanerService:
    """
    Construct a FolderCleanerService from a CleanerConfig.

    outlet_factory is a callable that accepts a NotificationConfig and returns
    a NotificationOutlet. Defaults to console outlet for any configured notification.
    """
    outlets: List[NotificationOutlet] = []
    for notif in config.notifications:
        if outlet_factory:
            outlet = outlet_factory(notif)
        else:
            if notif.type == "macos":
                outlet = MacOSNotificationOutlet(
                    title=notif.options.get("title", "ChronoSweep"),
                    subtitle=notif.options.get("subtitle"),
                    sound=notif.options.get("sound"),
                )
            else:
                outlet = ConsoleNotificationOutlet()
        if outlet:
            outlets.append(outlet)

    return FolderCleanerService(
        rules=config.rules,
        trash_dir=config.trash_dir,
        outlets=outlets,
    )


def _load_yaml_or_json(text: str) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return json.loads(text)
    else:
        return yaml.safe_load(text)


def _parse_retention(item: dict) -> timedelta:
    if "retention_time" in item:
        return _parse_duration(item["retention_time"])
    if "retention" in item:
        return _parse_duration(item["retention"])
    if "retention_days" in item:
        return timedelta(days=float(item["retention_days"]))
    raise ValueError("Retention is required (use retention_time like '5d', '12h', or retention_days).")


def _parse_duration(value) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(days=float(value))
    if not isinstance(value, str):
        raise ValueError("retention_time must be a string like '5d', '12h', or '1y'")

    text = value.strip().lower()
    match = None
    for pattern in (r"^(\d+)([hdwy])$", r"^(\d+)$"):
        match = re.match(pattern, text)
        if match:
            break
    if not match:
        raise ValueError("retention_time must be like '5d', '12h', '1y'")

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
    raise ValueError("Unsupported retention_time unit; use h, d, w, or y")

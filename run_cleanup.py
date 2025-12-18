from __future__ import annotations

import argparse
from pathlib import Path

from folder_cleaner import build_service_from_config, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FolderCleaner once.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "config.yaml",
        help="Path to config file (YAML/JSON).",
    )
    parser.add_argument(
        "--alerts",
        action="store_true",
        help="Also emit notifications/alerts for the configured window.",
    )
    parser.add_argument(
        "--alert-window-days",
        type=int,
        default=1,
        help="How many days ahead to look for alerts (default: 1).",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    service = build_service_from_config(cfg)

    removed = service.cleanup()
    print(f"Removed {len(removed)} items.")
    for path in removed:
        print(f"  {path}")

    if args.alerts:
        service.send_notifications(window_days=args.alert_window_days)


if __name__ == "__main__":
    main()

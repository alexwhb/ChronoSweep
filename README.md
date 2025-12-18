# ChronoSweep

ChronoSweep is a small, time-aware janitor for the folders you ignore. Point it at directories (Downloads, Desktop, scratch space), set retention windows and per-pattern overrides, and let it delete or trash what has expired—optionally alerting you before it does.

## Features
- Per-folder retention rules with pattern-specific overrides (e.g., shorter life for screenshots).
- Multiple actions: delete, move to a ChronoSweep trash folder, or move to the system trash.
- Pre-expiry notifications with multiple offsets (hours or days) and pluggable outlets (console by default).
- Configurable via a compact YAML/JSON file that works even without PyYAML installed.
- Simple CLI runner for ad-hoc or scheduled cleanup.
- Optional macOS Notification Center alerts.

## Quickstart
Requirements: Python 3.10+.

1) Create or edit a config file (see below). The repo ships with `config.yaml` as a starting point.
2) Run a cleanup:
```bash
python run_cleanup.py --config config.yaml
```
3) To emit alerts for items expiring soon (without deleting them immediately), add `--alerts` and optionally adjust the lookahead window:
```bash
python run_cleanup.py --config config.yaml --alerts --alert-window-days 3
```

## Configuration
ChronoSweep reads a YAML file; it also accepts JSON-compatible content for environments without PyYAML. Top-level keys:
- `trash_dir` (path): Where to place files when using the `trash` action. Defaults to `~/.folder_cleaner_trash`.
- `notifications` (list): Each entry has a `type` plus optional `options`. The built-in type is `console`.
- `rules` (list): One or more folder rules.

Rule fields:
- `path` (required): Folder to manage.
- `retention_time` | `retention` | `retention_days` (required): How long to keep items. Accepts strings like `6h`, `5d`, `2w`, `1y` or numbers (days).
- `notify_before`: Offsets before deletion to warn. Accepts a single value or list; strings like `4h`/`2d` or numbers (days). Defaults to `[0]` (alert on deletion day).
- `action`: `delete` (default), `trash`, or `system_trash`.
- `exemptions`: File or subpath names to keep (exact names or relative paths).
- `patterns`: Optional list of pattern-specific rules. Each item supports `pattern` (regex against filename), `retention_time`, `notify_before`, and `action`. Pattern rules override the folder defaults for matching items.

Example:
```yaml
trash_dir: "~/.folder_cleaner_trash"
notifications:
  - type: console
  - type: macos
    options:
      title: "ChronoSweep"
      subtitle: "Folder cleanup"
      sound: "Glass"
rules:
  - path: "~/Downloads"
    retention_time: "6h"
    notify_before: ["1h"]
    action: system_trash
    patterns:
      - pattern: "^ScreenShot"
        retention_time: "3h"
        notify_before: ["1h", "2h"]
  - path: "~/Desktop"
    retention_time: "24h"
    notify_before: ["4h"]
    action: trash
    exemptions:
      - "Keep me.txt"
      - "do-not-touch/"
```

### Trash behaviors
- `delete`: Remove immediately.
- `trash`: Move into `trash_dir` under a mirror of the folder structure.
- `system_trash`: Move into the OS trash (uses `$HOME/.Trash` on macOS). Override with `FOLDERCLEANER_SYSTEM_TRASH_OVERRIDE=/custom/trash`.

### Notification outlets
- `console`: Default; prints alerts to stdout (or your launchd log).
- `macos`: Uses Notification Center via `osascript`. Options: `title`, `subtitle`, `sound` (optional sound name, e.g., `"Glass"`).

## Scheduling
ChronoSweep does not run as a daemon; schedule it with your preferred tool:
- macOS/Linux cron: `0 * * * * /usr/bin/python /path/to/run_cleanup.py --config /path/to/config.yaml >> /tmp/chrono_sweep.log 2>&1`
- systemd timers, launchd jobs, or any CI/automation runner also work.

### launchd setup (macOS)
Example config we use for hourly runs:
- Plist path: `~/Library/LaunchAgents/com.chronosweep.cleanup.plist`
- Label: `com.chronosweep.cleanup`
- Schedule: top of every hour (`StartCalendarInterval` Minute=0)
- Command: `/Users/alexblack/Projects/Scripts & Other/FolderCleaner/.venv/bin/python /Users/alexblack/Projects/Scripts & Other/FolderCleaner/run_cleanup.py --config /Users/alexblack/Projects/Scripts & Other/FolderCleaner/config.yaml --alerts --alert-window-days 3`
- Environment: PATH set to `/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`
- Logs: stdout -> `/tmp/chronosweep.log`, stderr -> `/tmp/chronosweep.err`

Plist contents:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.chronosweep.cleanup</string>
  <key>StartCalendarInterval</key><dict><key>Minute</key><integer>0</integer></dict>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/alexblack/Projects/Scripts &amp; Other/FolderCleaner/.venv/bin/python</string>
    <string>/Users/alexblack/Projects/Scripts &amp; Other/FolderCleaner/run_cleanup.py</string>
    <string>--config</string>
    <string>/Users/alexblack/Projects/Scripts &amp; Other/FolderCleaner/config.yaml</string>
    <string>--alerts</string>
    <string>--alert-window-days</string>
    <string>3</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>/tmp/chronosweep.log</string>
  <key>StandardErrorPath</key><string>/tmp/chronosweep.err</string>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
```

Manage it:
- Load: `launchctl load ~/Library/LaunchAgents/com.chronosweep.cleanup.plist`
- Start now: `launchctl start com.chronosweep.cleanup`
- Check: `launchctl list | grep chronosweep`
- Logs: `tail -f /tmp/chronosweep.log /tmp/chronosweep.err`
- Update plist: edit, then `launchctl unload ...` and `launchctl load ...`
Notes: Using the venv interpreter ensures PyYAML and dependencies are available. If you move the repo or venv, update the paths above.

## Extending notifications
Notifications are pluggable via `NotificationOutlet`. The console outlet is the default. To wire a custom outlet (e.g., Slack, email), implement `send(alerts_by_date)` and pass it via `build_service_from_config` (see `folder_cleaner/config.py`).

## Development
- Run tests: `python -m unittest`
- Lint/format: not configured; use your preferred tools.

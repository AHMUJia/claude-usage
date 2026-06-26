# Claude Usage Screen

[中文](README.md) · **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![PySide6](https://img.shields.io/badge/GUI-PySide6-41cd52.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

A minimalist, monochrome **info screen** for a spare monitor or small HDMI
display. It shows a big clock, your **Claude Code usage** (5‑hour session and
weekly limits, as *% left* with reset times), and the current **weather**.

> **2.0**: adds a **Claude traffic light** next to the clock — green = idle /
> working, yellow = a notification, red = needs your permission or hit an error.
> Want the original light‑free version? Use the
> [`v1.0`](https://github.com/AHMUJia/claude-usage/releases/tag/v1.0) tag or the
> `main` branch.

<p align="center">
  <img src="docs/device.jpg" width="420" alt="concept render">
</p>

> Above: a concept render of the device on a desk. Below: the **actual rendered
> screen**:

![screenshot](docs/screenshot.png)

Built with PySide6 — a single Python file, no web stack, runs on
Windows / macOS / Linux.

## Quick start

```bash
git clone git@github.com:AHMUJia/claude-usage.git
cd claude-usage
pip install PySide6              # + `pip install claude-usage-widget` for the usage cards
python infoscreen.py --fullscreen
```

**Keys:** `F` / `F11` toggle fullscreen · `Esc` / `Q` quit.

## Features

- **Big clock** in a camera‑style "viewfinder" frame (24h or 12h).
- **Claude usage cards** — `5H LIMIT` (current session) and `WEEK LIMIT`
  (weekly, all models): percentage **left**, a progress bar, and the **reset**
  time. Powered by [`claude-usage-widget`](https://pypi.org/project/claude-usage-widget/).
- **Weather** in the top‑right: a hand‑drawn icon (sunny / cloudy / rain /
  snow / thunder / fog) plus temperature and humidity, from
  [wttr.in](https://wttr.in) (no API key, stdlib only).
- **Claude traffic light (2.0)**: three lamps next to the clock showing Claude
  Code's current state — green = idle/working, yellow = a notification, red =
  waiting for permission / errored. See
  [Claude traffic light](#claude-traffic-light-agent-signal-20).
- Pure monochrome, full‑screen friendly, fully self‑contained.

## Install

```bash
pip install PySide6
# optional, for the usage cards (requires Claude Code installed & logged in):
pip install claude-usage-widget
```

## Run

```bash
python infoscreen.py                 # windowed
python infoscreen.py --fullscreen    # full screen
python infoscreen.py --city Tokyo    # set the weather city
```

## Configure

Copy `config.example.json` to `config.json` (next to `infoscreen.py`) and edit:

| key | default | meaning |
|---|---|---|
| `weather_city` | `""` | wttr.in city; empty = auto‑detect by IP |
| `weather_refresh_seconds` | `900` | how often to refresh weather |
| `claude_refresh_seconds` | `300` | how often to refresh usage |
| `claude_usage_cmd` | `null` | command that prints the usage JSON; `null` = `python -m claude_usage --once` |
| `time_24h` | `true` | 24‑hour vs 12‑hour clock |
| `fullscreen` | `false` | start in fullscreen |
| `frameless` | `false` | borderless window |
| `width` / `height` | `960` / `640` | window size |
| `session_label` / `week_label` | `5H LIMIT` / `WEEK LIMIT` | card titles |
| `font_family` | `Bahnschrift` | condensed look on Windows; falls back elsewhere |
| `signal_enabled` | `true` | show the Claude traffic light |
| `signal_state_file` | `null` | status file path; `null` = auto per platform (see below) |
| `signal_poll_ms` | `700` | how often to re‑read the status file (ms) |
| `signal_show_idle` | `true` | steady green when nothing is running |
| `signal_completed_ttl` | `60` | green "done" returns to idle after this many seconds |
| `signal_session_ttl` | `1800` | a stuck working/idle session is dropped after 30 min |
| `signal_review_ttl` | `300` | a yellow notification auto‑clears after 5 min |
| `signal_problem_ttl` | `3600` | red (permission/error) is kept up to 1 h so you still see it after stepping away |

You can also pass `--config path/to/config.json`.

## Where do the usage numbers come from?

The two LIMIT cards run `python -m claude_usage --once` and read the JSON it
prints (`session_utilization`, `weekly_utilization`, `session_reset`,
`weekly_reset`). That tool reads **your local Claude Code data** and reuses
Claude Code's own login — nothing is sent anywhere by this app. If
`claude-usage-widget` isn't installed, the cards simply show `--` and the clock
and weather still work.

> Tip: if your Claude config lives in a non‑default directory, configure
> `claude-usage-widget` accordingly (see its docs), or point `claude_usage_cmd`
> at your own script that emits the same JSON.

## Claude traffic light (agent signal, 2.0)

The three lamps next to the clock reflect **what Claude Code is doing right now**:

| lamp | meaning | typical trigger |
|---|---|---|
| 🟢 green | idle / thinking / working / just finished | no session, `UserPromptSubmit`, `PreToolUse`, `Stop` (success) |
| 🟡 yellow | ordinary notification / needs a look | `Notification` (plain) |
| 🔴 red | waiting for your permission / blocked or errored | `Notification` (permission/approve), failed `Stop`, tool error |

All lamps are **steady (no blinking)** to avoid distraction. Aggregation is
**newest‑wins**: the light follows your most recent action. When a session goes
quiet, each state has a fallback lifetime — a yellow notification clears after
5 min, while **red is kept for up to 1 hour** so you still see the alert after
stepping away. All tunable in `config.json`.

### How it works

```
Claude Code fires a hook ──> agent_signal_hook.py ──writes──> status.json ──read──> traffic light
```

On each event Claude Code pipes the event JSON to `agent_signal_hook.py`, which
maps event → signal → state and atomically writes a `status.json`;
`infoscreen.py` polls that file and draws the lamps. With no file, the light
just stays green ("nothing to do").

> The status‑file schema and event mapping are compatible with
> **[ridyang/Agent-Signal-Bar](https://github.com/ridyang/Agent-Signal-Bar)** —
> all credit for the traffic‑light concept and schema goes there. This is a
> tiny, dependency‑free re‑implementation of just the **writer** side, so you
> **don't need to install that app** to drive this screen's light.

### Installing the hook

Put `agent_signal_hook.py` anywhere, then add a hook per event in Claude Code's
`settings.json` (e.g. `~/.claude/settings.json`), passing the event name as the
argument:

```jsonc
{
  "hooks": {
    "PreToolUse":  [ { "matcher": "", "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py PreToolUse",  "timeout": 5 } ] } ],
    "PostToolUse": [ { "matcher": "", "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py PostToolUse", "timeout": 5 } ] } ],
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py UserPromptSubmit", "timeout": 5 } ] } ],
    "Notification": [ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py Notification", "timeout": 10 } ] } ],
    "Stop":        [ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py Stop", "timeout": 5 } ] } ],
    "SessionStart":[ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py SessionStart", "timeout": 5 } ] } ],
    "SubagentStop":[ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py SubagentStop", "timeout": 5 } ] } ],
    "PreCompact":  [ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py PreCompact", "timeout": 5 } ] } ],
    "SessionEnd":  [ { "hooks": [ { "type": "command", "command": "python /path/to/agent_signal_hook.py SessionEnd", "timeout": 5 } ] } ]
  }
}
```

- **Windows**: prefer `pythonw.exe` (no console window flashing on your main
  screen); if the path has non‑ASCII characters, keep the script on an ASCII path.
- Hooks load at **session start** — reopen Claude Code after editing
  `settings.json` to see the light react.
- Status file location (override with `AGENT_SIGNAL_LIGHT_STATE_FILE`):
  - Windows: `%LOCALAPPDATA%\AgentSignalBar\status.json`
  - others: `/tmp/agent-signal/status.json`

Don't want the light? Set `signal_enabled` to `false` in `config.json`, or just
don't install the hook (the light stays green).

## License

MIT — see [LICENSE](LICENSE).

The traffic‑light concept and status schema come from
[ridyang/Agent-Signal-Bar](https://github.com/ridyang/Agent-Signal-Bar) (also MIT).

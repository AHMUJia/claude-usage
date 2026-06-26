#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code hook that writes the agent's current activity to a small status.json
which the traffic light in `infoscreen.py` reads.

The status-file schema and the event -> signal mapping are compatible with
**ridyang/Agent-Signal-Bar** (https://github.com/ridyang/Agent-Signal-Bar);
all credit for the traffic-light concept and schema goes there. This is a tiny,
dependency-free re-implementation of just the writer side, so you don't need to
install that app to drive `infoscreen.py`'s light.

State file (override with $AGENT_SIGNAL_LIGHT_STATE_FILE):
  Windows : %LOCALAPPDATA%\\AgentSignalBar\\status.json
  others  : /tmp/agent-signal/status.json

Install (merge into your Claude Code settings, e.g. ~/.claude/settings.json) a
hook entry per event that runs this script; the event name is passed as argv[1]
(and is also read from the hook JSON on stdin as a fallback). Example for one
event (use pythonw.exe on Windows to avoid a console window flashing):

  "PreToolUse": [
    { "matcher": "",
      "hooks": [ { "type": "command",
                   "command": "python /path/to/agent_signal_hook.py PreToolUse",
                   "timeout": 5 } ] }
  ]

Recommended events: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, PreCompact, SubagentStop, Stop, SessionEnd.

The script is intentionally silent and always exits 0 so it can never disturb
a Claude Code session.
"""
import os
import sys
import json
import time
import calendar
import uuid


def default_state_file():
    env = os.environ.get("AGENT_SIGNAL_LIGHT_STATE_FILE")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "AgentSignalBar", "status.json")
    return "/tmp/agent-signal/status.json"


STATE_FILE = default_state_file()
EVENT_LIMIT = 50
AGENT = "claude-code"
SESSION_MAX_AGE = 3600        # drop dead sessions older than this when writing

# --- Claude Code event name -> signal (from Agent Signal Bar's adapter) ----- #
EVENT_TO_SIGNAL = {
    "ConfigChange": "attention",
    "CwdChanged": "attention",
    "Elicitation": "attention",
    "ElicitationResult": "working",
    "FileChanged": "attention",
    "InstructionsLoaded": "attention",
    "SessionStart": "session_start",
    "TaskCreated": "subagent_start",
    "TaskCompleted": "subagent_stop",
    "TeammateIdle": "idle",
    "UserPromptExpansion": "thinking",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PostToolBatch": "tool_done",
    "PostToolUse": "tool_done",
    "PostToolUseFailure": "blocked",
    "PreCompact": "working",
    "PostCompact": "tool_done",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "PermissionRequest": "permission_request",
    "PermissionDenied": "blocked",
    "Notification": "notification",
    "Stop": "done",
    "StopFailure": "blocked",
    "WorktreeCreate": "working",
    "WorktreeRemove": "attention",
    "SessionEnd": "session_end",
}

SIGNAL_TO_DISPLAY = {
    "idle": "ready", "session_start": "ready", "session_end": "ready", "turn_end": "ready",
    "thinking": "active", "working": "active", "tool_done": "active",
    "subagent_start": "active", "subagent_stop": "active",
    "done": "completed",
    "attention": "needs_review", "notification": "needs_review",
    "permission": "permission", "permission_request": "permission",
    "blocked": "blocked", "failure": "blocked", "error": "blocked",
    "exception": "blocked", "max_tokens": "blocked",
    "stale": "stale", "off": "paused", "pause": "paused", "paused": "paused",
}
DISPLAY_PRIORITY = {
    "paused": 100, "blocked": 90, "permission": 80, "needs_review": 70,
    "stale": 60, "active": 50, "completed": 40, "ready": 0,
}


def norm_event(s):
    return "".join(ch for ch in (s or "").strip().lower() if ch.isalnum())


_CANON = {norm_event(k): k for k in EVENT_TO_SIGNAL}


def is_failure_word(s):
    s = (s or "").lower()
    return ("error" in s) or ("failed" in s) or ("failure" in s) or ("exception" in s)


def first_str(payload, keys):
    if not isinstance(payload, dict):
        return None
    want = {norm_event(k) for k in keys}
    for k, v in payload.items():
        if norm_event(k) in want and isinstance(v, (str, int, float, bool)):
            sv = str(v).strip()
            if sv:
                return sv
    return None


def contains_failure_marker(payload):
    fk = {"error", "failure", "exception", "errortype", "errormessage",
          "failurereason", "exitstatus", "toolerror"}
    if isinstance(payload, dict):
        for k, v in payload.items():
            nk = norm_event(k)
            if (nk in fk or is_failure_word(nk)) and _failure_value(v):
                return True
            if nk in ("context", "data", "detail", "details", "event", "hook",
                      "metadata", "meta", "payload", "request", "response",
                      "result", "run", "session", "source", "tool", "transcript"):
                if contains_failure_marker(v):
                    return True
    elif isinstance(payload, list):
        return any(contains_failure_marker(x) for x in payload)
    return False


def _failure_value(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        n = v.strip().lower()
        if n in ("", "0", "false", "no", "none", "null", "success", "ok"):
            return False
        return True
    return v is not None


def choose_signal(event_name, payload):
    explicit = first_str(payload, ["signal", "signal_name", "lamp_signal"])
    if explicit and norm_event(explicit) in SIGNAL_TO_DISPLAY:
        return norm_event(explicit)
    status = first_str(payload, ["status", "state"])
    if status:
        ns = norm_event(status)
        if ns in SIGNAL_TO_DISPLAY:
            return ns
        if is_failure_word(status):
            return "blocked"
    if contains_failure_marker(payload):
        return "blocked"
    resolved = event_name or first_str(
        payload, ["hook_event_name", "event_name", "event", "hook", "type", "name"]) or "Stop"
    if norm_event(resolved) == norm_event("Stop"):
        sr = first_str(payload, ["stop_reason"])
        if sr:
            nsr = norm_event(sr)
            if nsr in ("maxtokens",):
                return "max_tokens"
            if is_failure_word(nsr):
                return "error"
    if norm_event(resolved) == norm_event("Notification"):
        msg = (first_str(payload, ["message", "title", "body", "text"]) or "").lower()
        if ("permission" in msg or "approve" in msg or "approval" in msg
                or "allow" in msg):
            return "permission_request"
        return "notification"
    sig = EVENT_TO_SIGNAL.get(_CANON.get(norm_event(resolved)))
    return sig or "attention"


def session_key(payload):
    for k in ("session_id", "conversation_id", "thread_id", "chat_id", "claude_session_id"):
        v = first_str(payload, [k])
        if v:
            return v
    tp = first_str(payload, ["transcript_path"])
    if tp:
        return "transcript:" + os.path.basename(tp.strip())
    cwd = first_str(payload, ["cwd", "workspace", "workspace_dir", "project_dir"])
    if cwd:
        return "cwd:" + cwd.strip()
    return "claude-global"


def load_doc():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("sessions", {})
            d.setdefault("events", [])
            if not isinstance(d["sessions"], dict):
                d["sessions"] = {}
            if not isinstance(d["events"], list):
                d["events"] = []
            return d
    except Exception:
        pass
    return {"schema_version": 1, "aggregate": "idle", "sessions": {}, "events": []}


def atomic_write(doc):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def main():
    argv_event = sys.argv[1] if len(sys.argv) > 1 else None

    raw = b""
    try:
        if sys.stdin is not None and hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read()
    except Exception:
        raw = b""
    if not raw:
        try:
            chunks = []
            while True:
                c = os.read(0, 65536)
                if not c:
                    break
                chunks.append(c)
            raw = b"".join(chunks)
        except Exception:
            raw = b""

    payload = {}
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8", "ignore")) or {}
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

    event_name = argv_event or first_str(
        payload, ["hook_event_name", "event_name", "event", "hook", "type", "name"]) or "Stop"
    signal = choose_signal(event_name, payload)
    disp = SIGNAL_TO_DISPLAY.get(signal, "needs_review")
    key = session_key(payload)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    doc = load_doc()
    sessions = doc["sessions"]

    # Drop dead sessions (closed windows etc.) so an old notification/permission
    # cannot linger in the file forever. The session we're about to write stays.
    now_ep = calendar.timegm(time.strptime(now, "%Y-%m-%dT%H:%M:%SZ"))
    for k in list(sessions.keys()):
        if k == key:
            continue
        try:
            age = now_ep - calendar.timegm(
                time.strptime(sessions[k].get("updated_at", ""), "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            age = SESSION_MAX_AGE + 1
        if age > SESSION_MAX_AGE:
            sessions.pop(k, None)

    existing = sessions.get(key)
    ex_disp = SIGNAL_TO_DISPLAY.get((existing or {}).get("signal"), None) if existing else None

    ne = norm_event(event_name)
    if ne == norm_event("SessionEnd"):
        # only clear ordinary active sessions; keep ones that still need handling
        if ex_disp in ("permission", "blocked", "needs_review", "completed"):
            pass
        else:
            sessions.pop(key, None)
    elif disp in ("ready", "completed") and ex_disp in ("permission", "blocked", "needs_review"):
        # a completion/idle event must not bury a state that still needs handling
        pass
    else:
        sessions[key] = {
            "agent": AGENT, "signal": signal,
            "last_event": event_name, "updated_at": now,
        }

    doc["events"].insert(0, {
        "id": str(uuid.uuid4()).upper(), "session_id": key, "agent": AGENT,
        "signal": signal, "event": event_name, "updated_at": now,
    })
    doc["events"] = doc["events"][:EVENT_LIMIT]

    best = None
    for s in sessions.values():
        dsp = SIGNAL_TO_DISPLAY.get(s.get("signal"), "ready")
        pr = DISPLAY_PRIORITY.get(dsp, 0)
        if best is None or pr > best[0]:
            best = (pr, s.get("signal"))
    doc["schema_version"] = 1
    doc["aggregate"] = best[1] if best else "idle"
    doc["updated_at"] = now

    try:
        atomic_write(doc)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

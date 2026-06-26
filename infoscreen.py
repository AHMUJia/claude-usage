#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Usage Screen 2.0 — a minimalist, monochrome info display.

A single full-screen panel showing:
  - a big HH:MM clock in a "viewfinder" frame
  - a Claude Code "traffic light" (agent status) next to the clock: green when
    Claude is idle / working, yellow for a notification, red when it needs your
    permission or hit an error
  - your Claude Code usage as two cards: 5H LIMIT (current 5-hour session) and
    WEEK LIMIT (weekly, all models), each as "% LEFT" + a bar + the reset time
  - the current weather (icon + temperature + humidity) in the top-right

Great as a desk clock on a spare monitor / small HDMI screen.

The traffic light reads a small JSON status file that Claude Code hooks keep
up to date (see `agent_signal_hook.py`). The status-file schema and the
event -> signal mapping are compatible with the open-source project
**ridyang/Agent-Signal-Bar** (https://github.com/ridyang/Agent-Signal-Bar) —
all credit for the traffic-light concept goes there. If no status file exists,
the light just stays steady green ("nothing to do").

Dependencies:
  - PySide6                       (pip install PySide6)            -- required
  - claude-usage-widget           (pip install claude-usage-widget) -- optional,
        provides the usage numbers via `python -m claude_usage --once`.
        Without it, the two LIMIT cards just show "--".
  - Weather uses wttr.in over plain HTTPS (Python stdlib, no extra deps).

Controls:  F / F11 = toggle fullscreen   ·   Esc / Q = quit

Config: optional `config.json` next to this file (see config.example.json).
CLI:    --city "Tokyo"  --fullscreen  --config path/to/config.json
"""
import os
import sys
import json
import time
import calendar
import argparse
import urllib.request

from PySide6.QtCore import (Qt, QTimer, QDateTime, QRectF, QThread, Signal,
                            QProcess, QProcessEnvironment)
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

HERE = os.path.dirname(os.path.abspath(__file__))


def default_state_file():
    """Where the traffic-light status JSON lives (matches Agent Signal Bar)."""
    env = os.environ.get("AGENT_SIGNAL_LIGHT_STATE_FILE")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "AgentSignalBar", "status.json")
    return "/tmp/agent-signal/status.json"


DEFAULTS = {
    # Weather (wttr.in). Empty city = auto-detect by your IP.
    "weather_city": "",
    "weather_refresh_seconds": 900,
    # Claude usage refresh.
    "claude_refresh_seconds": 300,
    # Command that prints the claude-usage-widget JSON. null = auto:
    #   [<this python>, "-m", "claude_usage", "--once"]
    "claude_usage_cmd": None,
    # Display
    "time_24h": True,
    "fullscreen": False,
    "frameless": False,
    "width": 960,
    "height": 640,
    "session_label": "5H LIMIT",
    "week_label": "WEEK LIMIT",
    # Font family (Windows ships "Bahnschrift" which gives the condensed look;
    # falls back to the platform default sans elsewhere).
    "font_family": "Bahnschrift",
    # --- Claude traffic light (agent status) ---------------------------- #
    # JSON file written by Claude Code hooks (see agent_signal_hook.py).
    # null = auto per platform (see default_state_file()).
    "signal_state_file": None,
    "signal_enabled": True,
    "signal_poll_ms": 700,            # how often to re-read the status file
    "signal_show_idle": True,         # steady green when nothing is running
    # Per-state "how long the light is kept when no newer event arrives" (s).
    # Aggregation is newest-wins: the light follows your most recent action;
    # these only matter once a session goes quiet.
    "signal_completed_ttl": 60,       # green "done" -> idle after 60s
    "signal_session_ttl": 1800,       # stuck working/idle dropped after 30min
    "signal_review_ttl": 300,         # yellow notification auto-clears after 5min
    "signal_problem_ttl": 3600,       # red (permission/blocked) kept up to 1h so
                                      # you still see the alert after stepping away
}


def load_config(path=None):
    cfg = dict(DEFAULTS)
    p = path or os.path.join(HERE, "config.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                u = json.load(f)
            if isinstance(u, dict):
                cfg.update(u)
        except (OSError, ValueError) as e:
            print("config load failed: %s" % e, file=sys.stderr)
    return cfg


# --------------------------------------------------------------------------- #
#  Weather worker (stdlib urllib, runs off the GUI thread)                     #
# --------------------------------------------------------------------------- #
class WeatherWorker(QThread):
    done = Signal(str, str, int)        # temp_C, humidity, weatherCode

    def __init__(self, city):
        super().__init__()
        self.city = city or ""

    def run(self):
        try:
            url = "https://wttr.in/%s?format=j1" % urllib.request.quote(self.city)
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=12) as r:
                cc = json.loads(r.read().decode("utf-8"))["current_condition"][0]
            self.done.emit(str(cc["temp_C"]), str(cc["humidity"]), int(cc["weatherCode"]))
        except Exception as e:  # noqa: BLE001 - network/parse errors are non-fatal
            print("weather fetch failed: %s" % e, file=sys.stderr)


# WWO weatherCode -> simple icon category
_WCODE = {
    "sun": {113},
    "partly": {116},
    "cloud": {119, 122},
    "fog": {143, 248, 260},
    "rain": {176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314,
             317, 320, 350, 353, 356, 359, 362, 365, 386, 389},
    "snow": {179, 182, 185, 227, 230, 323, 326, 329, 332, 335, 338, 368, 371,
             374, 377, 392, 395},
    "thunder": {200},
}


def _wcode_to_cond(code):
    return next((k for k, v in _WCODE.items() if code in v), "cloud")


# --------------------------------------------------------------------------- #
#  Claude traffic light                                                        #
# --------------------------------------------------------------------------- #
# Reads the status.json kept up to date by Claude Code hooks. The schema and the
# signal -> state mapping match ridyang/Agent-Signal-Bar.
SIG_DISPLAY = {
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
# display state -> (lamp index 0=red/1=yellow/2=green, flash none/slow/fast)
# All steady (none) by design — a blinking light is distracting on a desk.
SIG_LOOK = {
    "ready":        (2, "none"),
    "active":       (2, "none"),
    "completed":    (2, "none"),
    "needs_review": (1, "none"),
    "permission":   (0, "none"),
    "blocked":      (0, "none"),
    "stale":        (1, "none"),
    "paused":       (1, "none"),
}
SIG_LAMP_RGB = ["#ff453a", "#ffd60a", "#32d74b"]   # red / yellow / green


def _sig_parse_iso(s):
    """'2026-06-26T13:19:53Z' -> epoch seconds; 0 on failure."""
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return 0.0


class SignalLight(QWidget):
    """A three-lamp traffic light reflecting this machine's Claude Code state."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.path = cfg.get("signal_state_file") or default_state_file()
        self.completed_ttl = int(cfg.get("signal_completed_ttl", 60))
        self.session_ttl = int(cfg.get("signal_session_ttl", 1800))
        self.review_ttl = int(cfg.get("signal_review_ttl", 300))
        self.problem_ttl = int(cfg.get("signal_problem_ttl", 3600))
        self.show_idle = bool(cfg.get("signal_show_idle", True))
        # Per-state expiry (see DEFAULTS). Anything not listed uses session_ttl.
        self._ttl = {
            "completed": self.completed_ttl,
            "needs_review": self.review_ttl,
            "permission": self.problem_ttl,
            "blocked": self.problem_ttl,
        }
        self.disp = "ready"
        self.sig = "idle"
        self.flash = "none"
        self.lamp = 2
        self.nsess = 0
        self._fail = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh)
        self.poll.start(max(200, int(cfg.get("signal_poll_ms", 700))))
        self.blink = QTimer(self)
        self.blink.timeout.connect(self._tick)
        self.blink.start(130)
        QTimer.singleShot(300, self.refresh)

    def _tick(self):
        if self.flash != "none":          # only flashing states need fast repaint
            self.update()

    def refresh(self):
        disp, sig, n = self._read()
        if (disp, sig, n) != (self.disp, self.sig, self.nsess):
            self.disp, self.sig, self.nsess = disp, sig, n
            self.lamp, self.flash = SIG_LOOK.get(disp, SIG_LOOK["ready"])
            self.update()

    def _read(self):
        """status.json -> (display_state, signal, active session count).

        Aggregation is newest-wins: among non-expired sessions, the one updated
        most recently drives the light. Expiry (self._ttl) is only a fallback for
        when a session goes quiet, so a stale notification/error does not linger
        forever (but a real red alert is kept up to problem_ttl)."""
        if not self.path or not os.path.exists(self.path):
            self._fail = 0
            return ("ready", "idle", 0)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._fail = 0
        except Exception:
            # may have hit the atomic write mid-flight; only after a few
            # consecutive failures do we call it stale
            self._fail += 1
            if self._fail >= 3:
                return ("stale", "stale", self.nsess)
            return (self.disp, self.sig, self.nsess)
        now = time.time()
        best = None   # (updated_epoch, disp, sig)
        nact = 0
        for s in (d.get("sessions") or {}).values():
            sig = s.get("signal")
            disp = SIG_DISPLAY.get(sig)
            if disp is None:
                continue
            ts = _sig_parse_iso(s.get("updated_at", ""))
            if now - ts > self._ttl.get(disp, self.session_ttl):
                continue
            nact += 1
            if best is None or ts > best[0]:
                best = (ts, disp, sig)
        if best is None:
            return ("ready", "idle", 0)
        return (best[1], best[2], nact)

    def paintEvent(self, _e):
        W, H = self.width(), self.height()
        if W < 20 or H < 20:
            return
        if self.disp == "ready" and not self.show_idle:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = min(H * 0.44, W * 0.21)             # lamp diameter
        gap = d * 0.44
        lamps_w = 3 * d + 2 * gap
        x0 = (W - lamps_w) / 2.0
        cy = H / 2.0
        # housing
        pad = d * 0.30
        hh = d + pad * 1.5
        hrect = QRectF(x0 - pad, cy - hh / 2, lamps_w + 2 * pad, hh)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(hrect, hh * 0.5, hh * 0.5)
        # three lamps
        for i in range(3):
            cx = x0 + d / 2 + i * (d + gap)
            base = QColor(SIG_LAMP_RGB[i])
            lit = (i == self.lamp)
            if lit:
                glow = QColor(base); glow.setAlpha(85)
                p.setBrush(glow); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - d * 0.72, cy - d * 0.72, d * 1.44, d * 1.44))
                col = base
            else:
                col = QColor(base).darker(360)
                col.setAlpha(150)
            p.setBrush(col)
            p.setPen(QPen(QColor(0, 0, 0, 120), max(1, d * 0.06)))
            p.drawEllipse(QRectF(cx - d / 2, cy - d / 2, d, d))


# --------------------------------------------------------------------------- #
#  The info panel                                                              #
# --------------------------------------------------------------------------- #
class InfoPage(QWidget):
    WK = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.proc = None
        self.weather = None
        self.plan = ""
        self.sess_left = None
        self.week_left = None
        self.sess_reset = 0
        self.week_reset = 0
        self.temp = "--"
        self.humi = "--"
        self.wcond = ""
        self.setStyleSheet("background:#000000;")

        t = QTimer(self)
        t.timeout.connect(self.update)                 # repaint clock each second
        t.start(1000)

        self.ctimer = QTimer(self)
        self.ctimer.timeout.connect(self.refresh_claude)
        self.ctimer.start(max(60, int(cfg["claude_refresh_seconds"])) * 1000)
        QTimer.singleShot(800, self.refresh_claude)

        self.wtimer = QTimer(self)
        self.wtimer.timeout.connect(self.refresh_weather)
        self.wtimer.start(max(300, int(cfg["weather_refresh_seconds"])) * 1000)
        QTimer.singleShot(1500, self.refresh_weather)

        # Claude traffic light — a transparent child overlaid on the right of the
        # clock; positioned in resizeEvent so clock + light read as one group.
        self.signal = None
        if cfg.get("signal_enabled", True):
            self.signal = SignalLight(cfg, self)
            self.signal.show()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.signal:
            return
        W, H = self.width(), self.height()
        # Centered group inside the full-width viewfinder: clock box (gx + 0.34W)
        # + gap 0.025W + light box 0.40W. Keep in sync with paintEvent.
        self.signal.setGeometry(int(W * (0.1175 + 0.34 + 0.025)), int(H * 0.145),
                                int(W * 0.40), int(H * 0.32))
        self.signal.raise_()

    # ---- data ------------------------------------------------------------- #
    def _claude_cmd(self):
        cmd = self.cfg.get("claude_usage_cmd")
        if cmd:
            return (cmd[0], cmd[1:]) if isinstance(cmd, list) else (cmd, [])
        return (sys.executable, ["-m", "claude_usage", "--once"])

    def refresh_claude(self):
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        prog, args = self._claude_cmd()
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.proc.finished.connect(self._claude_done)
        self.proc.errorOccurred.connect(lambda *_: None)
        self.proc.start(prog, args)

    def _claude_done(self, *_):
        raw = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "ignore")
        try:
            d = json.loads(raw)
        except ValueError:
            return
        self.plan = (d.get("subscription_type") or "").upper()
        su = (d.get("session_utilization") or 0) * 100.0
        wu = (d.get("weekly_utilization") or 0) * 100.0
        self.sess_left = max(0, min(100, int(round(100 - su))))
        self.week_left = max(0, min(100, int(round(100 - wu))))
        self.sess_reset = d.get("session_reset") or 0
        self.week_reset = d.get("weekly_reset") or 0
        self.update()

    def refresh_weather(self):
        if self.weather is not None and self.weather.isRunning():
            return
        self.weather = WeatherWorker(self.cfg.get("weather_city", ""))
        self.weather.done.connect(self._weather_done)
        self.weather.start()

    def _weather_done(self, temp, humi, code):
        self.temp, self.humi, self.wcond = temp, humi, _wcode_to_cond(code)
        self.update()

    # ---- drawing helpers -------------------------------------------------- #
    def _font(self, px, bold=False):
        f = QFont(self.cfg.get("font_family", "Bahnschrift"))
        f.setPixelSize(max(8, int(px)))
        f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return f

    def _card(self, p, x, y, w, h, rad):
        p.setPen(QPen(QColor("#FFFFFF"), max(2, int(self.height() * 0.004))))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), rad, rad)

    def _limit_card(self, p, x, y, w, h, title, left, reset_epoch):
        AL = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._card(p, x, y, w, h, h * 0.12)
        pad = w * 0.07
        p.setPen(QColor("#FFFFFF"))
        p.setFont(self._font(h * 0.17, bold=True))
        p.drawText(QRectF(x + pad, y + h * 0.10, w - 2 * pad, h * 0.22), AL, title)
        val = "--" if left is None else "%d%%" % left
        p.setFont(self._font(h * 0.30, bold=True))
        fm = QFontMetrics(p.font())
        vy = y + h * 0.34
        p.drawText(QRectF(x + pad, vy, w - 2 * pad, h * 0.30), AL, val)
        vw = fm.horizontalAdvance(val)
        p.setFont(self._font(h * 0.14))
        p.drawText(QRectF(x + pad + vw + w * 0.06, vy, w - 2 * pad - vw, h * 0.30), AL, "LEFT")
        bx, bw = x + pad, w - 2 * pad
        by, bh = y + h * 0.66, h * 0.07
        p.setPen(QPen(QColor("#FFFFFF"), max(1, int(self.height() * 0.002))))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), bh / 2, bh / 2)
        if left is not None and left > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF(bx, by, max(bh, bw * left / 100.0), bh), bh / 2, bh / 2)
        rs = ""
        if reset_epoch:
            rs = "RESET " + QDateTime.fromSecsSinceEpoch(int(reset_epoch)).toString("MM/dd HH:mm")
        p.setPen(QColor("#FFFFFF"))
        p.setFont(self._font(h * 0.13))
        p.drawText(QRectF(x + pad, y + h * 0.80, w - 2 * pad, h * 0.16), AL, rs)

    def _cloud(self, p, x, y, s, color):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(x + s * 0.10, y + s * 0.36, s * 0.34, s * 0.34))
        p.drawEllipse(QRectF(x + s * 0.30, y + s * 0.22, s * 0.44, s * 0.44))
        p.drawEllipse(QRectF(x + s * 0.54, y + s * 0.38, s * 0.30, s * 0.30))
        p.drawRoundedRect(QRectF(x + s * 0.14, y + s * 0.52, s * 0.66, s * 0.22), s * 0.11, s * 0.11)

    def _weather_icon(self, p, x, y, s, cond):
        import math
        w = QColor("#FFFFFF")
        lw = max(2, int(s * 0.09))
        cx, cy = x + s / 2, y + s / 2
        if cond == "sun":
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(w)
            r = s * 0.24
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
            p.setPen(QPen(w, lw)); p.setBrush(Qt.BrushStyle.NoBrush)
            for k in range(8):
                a = k * math.pi / 4
                p.drawLine(int(cx + s * 0.34 * math.cos(a)), int(cy + s * 0.34 * math.sin(a)),
                           int(cx + s * 0.47 * math.cos(a)), int(cy + s * 0.47 * math.sin(a)))
        elif cond == "partly":
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(w)
            r = s * 0.18
            sc, scy = x + s * 0.16 + r, y + s * 0.12 + r
            p.drawEllipse(QRectF(x + s * 0.16, y + s * 0.12, 2 * r, 2 * r))
            p.setPen(QPen(w, max(1, int(s * 0.06)))); p.setBrush(Qt.BrushStyle.NoBrush)
            for k in range(8):
                a = k * math.pi / 4
                p.drawLine(int(sc + s * 0.26 * math.cos(a)), int(scy + s * 0.26 * math.sin(a)),
                           int(sc + s * 0.34 * math.cos(a)), int(scy + s * 0.34 * math.sin(a)))
            self._cloud(p, x + s * 0.06, y + s * 0.10, s * 0.92, w)
        elif cond == "rain":
            self._cloud(p, x, y - s * 0.10, s, w)
            p.setPen(QPen(w, lw))
            for dx in (0.30, 0.50, 0.70):
                p.drawLine(int(x + s * dx), int(y + s * 0.74),
                           int(x + s * (dx - 0.06)), int(y + s * 0.94))
        elif cond == "snow":
            self._cloud(p, x, y - s * 0.10, s, w)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(w)
            for dx in (0.30, 0.50, 0.70):
                p.drawEllipse(QRectF(x + s * dx - s * 0.04, y + s * 0.78, s * 0.08, s * 0.08))
        elif cond == "thunder":
            self._cloud(p, x, y - s * 0.10, s, w)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(w)
            pts = [(0.50, 0.70), (0.38, 0.92), (0.50, 0.92), (0.42, 1.08),
                   (0.64, 0.82), (0.52, 0.82), (0.60, 0.70)]
            p.drawPolygon(QPolygonF([QPointF(x + s * a, y + s * b) for a, b in pts]))
        else:
            self._cloud(p, x, y, s, w)

    # ---- paint ------------------------------------------------------------ #
    def paintEvent(self, _e):
        W, H = self.width(), self.height()
        if W < 50:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(0, 0, W, H, QColor("#000000"))
        WHITE = QColor("#FFFFFF")
        M = int(W * 0.045)
        AR = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        AL = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        now = QDateTime.currentDateTime()
        d = now.date()

        # ---- top bar ----
        bar_h = H * 0.062
        bar_y = H * 0.035
        p.setPen(WHITE)
        p.setFont(self._font(bar_h * 0.95, bold=True))
        p.drawText(QRectF(M, bar_y, W * 0.5, bar_h), AL,
                   "%02d/%02d %s" % (d.month(), d.day(), self.WK[d.dayOfWeek() - 1]))
        p.setFont(self._font(bar_h * 0.92, bold=True))
        fm = QFontMetrics(p.font())
        htxt, ttxt = "%s%%" % self.humi, "%s°C" % self.temp
        wg = W * 0.018
        hw, tw = fm.horizontalAdvance(htxt), fm.horizontalAdvance(ttxt)
        rx = W - M
        p.drawText(QRectF(rx - hw, bar_y, hw, bar_h), AR, htxt)
        rx -= hw + wg
        p.drawText(QRectF(rx - tw, bar_y, tw, bar_h), AR, ttxt)
        rx -= tw + wg * 1.5
        ic = bar_h * 1.3
        self._weather_icon(p, rx - ic, bar_y + bar_h / 2 - ic / 2, ic, self.wcond or "cloud")

        # ---- clock (+ traffic light) inside the viewfinder ----
        # Full-width viewfinder; the clock sits in the left half of a centered
        # group and the traffic-light child (resizeEvent) in the right half.
        fx, fw = M, W - 2 * M
        fy, fh = H * 0.145, H * 0.32
        fmt = "HH:mm" if self.cfg.get("time_24h", True) else "h:mm"
        p.setPen(WHITE)
        if self.signal:
            gx = W * 0.1175                       # left edge of the centered group
            cbx, cbw = gx, W * 0.34               # clock box
            p.setFont(self._font(min(fh * 0.80, cbw * 0.38), bold=True))
            p.drawText(QRectF(cbx, fy, cbw, fh), int(Qt.AlignmentFlag.AlignCenter),
                       now.toString(fmt))
        else:
            p.setFont(self._font(fh * 0.80, bold=True))
            p.drawText(QRectF(fx, fy, fw, fh), int(Qt.AlignmentFlag.AlignCenter),
                       now.toString(fmt))
        cl = min(fw, fh) * 0.16
        pen = QPen(WHITE, max(3, int(H * 0.007)))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        for (cx, cy, sx, sy) in ((fx, fy, 1, 1), (fx + fw, fy, -1, 1),
                                 (fx, fy + fh, 1, -1), (fx + fw, fy + fh, -1, -1)):
            p.drawLine(int(cx), int(cy), int(cx + sx * cl), int(cy))
            p.drawLine(int(cx), int(cy), int(cx), int(cy + sy * cl))

        # ---- two LIMIT cards ----
        gap = W * 0.03
        cw = (W - 2 * M - gap) / 2
        cy, ch = H * 0.52, H * 0.41
        self._limit_card(p, M, cy, cw, ch, self.cfg.get("session_label", "5H LIMIT"),
                         self.sess_left, self.sess_reset)
        self._limit_card(p, M + cw + gap, cy, cw, ch, self.cfg.get("week_label", "WEEK LIMIT"),
                         self.week_left, self.week_reset)


# --------------------------------------------------------------------------- #
#  Window                                                                      #
# --------------------------------------------------------------------------- #
class MainWindow(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.setWindowTitle("Claude Usage Screen")
        flags = Qt.WindowType.Window
        if cfg.get("frameless"):
            flags |= Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(flags)
        self.resize(int(cfg.get("width", 960)), int(cfg.get("height", 640)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.page = InfoPage(cfg)
        lay.addWidget(self.page)
        if cfg.get("fullscreen"):
            self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        elif e.key() in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        else:
            super().keyPressEvent(e)


def main():
    ap = argparse.ArgumentParser(description="Claude Usage Screen")
    ap.add_argument("--config", help="path to a config.json")
    ap.add_argument("--city", help="weather city (overrides config)")
    ap.add_argument("--fullscreen", action="store_true", help="start fullscreen")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.city is not None:
        cfg["weather_city"] = args.city
    if args.fullscreen:
        cfg["fullscreen"] = True

    app = QApplication(sys.argv)
    win = MainWindow(cfg)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

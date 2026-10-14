"""Read subscription limits, without running inference or storing credentials."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import urllib.error
import urllib.request

POLL_SECONDS = 60
STALE_SECONDS = 180
COLORS = {"claude": "#F2A35B", "codex": "#45CF95"}
WARNING_COLOR = "#CE650C"
CRITICAL_COLOR = "#F04452"
UNKNOWN_COLOR = "#89929F"


class UsageError(Exception):
    def __init__(self, message: str, retry_after: float = POLL_SECONDS):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Window:
    remaining: float
    resets_at: float | None


@dataclass
class Snapshot:
    weekly: Window
    session: Window | None = None
    extra_usage: bool = False
    fetched_at: float = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class ProviderState:
    snapshot: Snapshot | None = None
    error: str | None = None
    next_poll: float = 0
    busy: bool = False
    failures: int = 0

    def stale(self, now=None):
        now = time.time() if now is None else now
        if self.snapshot is None or self.error:
            return True
        return (now - self.snapshot.fetched_at > STALE_SECONDS or
                (self.snapshot.weekly.resets_at is not None and
                 now >= self.snapshot.weekly.resets_at))

    def success(self, snapshot, now=None):
        self.snapshot = snapshot
        self.error = None
        self.failures = 0
        self.next_poll = (time.monotonic() if now is None else now) + POLL_SECONDS

    def failure(self, error, now=None):
        self.error = str(error)
        self.failures += 1
        delay = min(3600, POLL_SECONDS * 2 ** min(self.failures - 1, 6))
        # Respect server Retry-After, including values longer than an hour.
        self.next_poll = (time.monotonic() if now is None else now) + max(delay, error.retry_after)


def remaining_percent(used):
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
        raise UsageError("Anbieter liefert keinen gültigen Prozentwert.")
    return max(0.0, min(100.0, 100.0 - used))


def reset_timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    except (ValueError, OverflowError):
        pass
    raise UsageError("Anbieter liefert einen ungültigen Reset-Zeitpunkt.")


def parse_codex(result):
    by_id = result.get("rateLimitsByLimitId") or {}
    limits = by_id.get("codex") or result.get("rateLimits") or {}
    if limits.get("limitId") not in (None, "codex"):
        raise UsageError("Kein allgemeines Codex-Wochenlimit verfügbar.")
    weekly = session = None
    for name in ("primary", "secondary"):
        window = limits.get(name)
        if not isinstance(window, dict):
            continue
        duration = window.get("windowDurationMins")
        if duration not in (10080, 300):
            continue
        parsed = Window(remaining_percent(window.get("usedPercent")), reset_timestamp(window.get("resetsAt")))
        if duration == 10080:
            weekly = parsed
        elif duration == 300:
            session = parsed
    if weekly is None:
        raise UsageError("OpenAI liefert für dieses Konto kein Wochenlimit.")
    credits = limits.get("credits") or {}
    return Snapshot(weekly, session, bool(credits.get("hasCredits") or credits.get("unlimited")), time.time())


def parse_claude(result):
    weekly_data = result.get("seven_day")
    if not isinstance(weekly_data, dict):
        raise UsageError("Anthropic liefert für dieses Konto kein Wochenlimit.")
    weekly = Window(remaining_percent(weekly_data.get("utilization")), reset_timestamp(weekly_data.get("resets_at")))
    session_data = result.get("five_hour")
    session = None
    if isinstance(session_data, dict):
        session = Window(remaining_percent(session_data.get("utilization")), reset_timestamp(session_data.get("resets_at")))
    return Snapshot(weekly, session, bool((result.get("extra_usage") or {}).get("is_enabled")), time.time())


def fetch_codex():
    executable = shutil.which("codex")
    if not executable:
        raise UsageError("Codex ist nicht installiert.", 300)
    # Authentication and token renewal stay with the official CLI. No thread,
    # model call, tool execution, or credit-reset operation is created.
    process = subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=Path.home(), bufsize=0,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    deadline = time.monotonic() + 25

    def send(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        process.stdin.flush()

    def receive(request_id):
        while time.monotonic() < deadline:
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                try:
                    response = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        # Never display arbitrary backend text that might contain
                        # account details, credentials, or request headers.
                        raise UsageError("Codex-Abfrage fehlgeschlagen – Anmeldung in Codex prüfen.", 120)
                    return response.get("result") or {}
            if not selector.select(min(1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise UsageError("Codex-Verbindung wurde beendet.")
            pending.extend(chunk)
            if len(pending) > 2_000_000:
                raise UsageError("Unerwartete Codex-Antwort.")
        raise UsageError("Codex antwortet gerade nicht.")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "ai_limits", "title": "AI Limits", "version": "1.0.0"},
            "capabilities": {},
        }})
        receive(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        return parse_codex(receive(2))
    except (OSError, ValueError):
        raise UsageError("Codex-Verbindung fehlgeschlagen.") from None
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A bearer token must only go to the intended provider.
        raise UsageError("Unerwartete Weiterleitung bei Anthropic.", 300)


def fetch_claude():
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).expanduser()
    try:
        auth = json.loads((directory / ".credentials.json").read_text())["claudeAiOauth"]
        token = auth["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        raise UsageError("Bitte zuerst in Claude Code anmelden.", 120) from None
    if not isinstance(token, str) or not token:
        raise UsageError("Bitte zuerst in Claude Code anmelden.", 120)
    if auth.get("expiresAt") and auth["expiresAt"] < time.time() * 1000:
        raise UsageError("Claude-Anmeldung abgelaufen – Claude Code öffnen.", 120)
    request = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "ai-limits/1.0.0",
    })
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            data = json.loads(response.read(2_000_000))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise UsageError("Claude-Anmeldung erneuern – Claude Code öffnen.", 120) from None
        if error.code == 429:
            retry = 300.0
            value = error.headers.get("Retry-After", "")
            try:
                seconds = float(value)
                if math.isfinite(seconds):
                    retry = max(retry, seconds)
            except ValueError:
                from email.utils import parsedate_to_datetime
                try:
                    retry = max(retry, parsedate_to_datetime(value).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
            raise UsageError("Anthropic bremst die Abfrage; erneuter Versuch folgt.", retry) from None
        raise UsageError(f"Anthropic ist nicht erreichbar (HTTP {error.code}).") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise UsageError("Keine Verbindung zu Anthropic.") from None
    if not isinstance(data, dict):
        raise UsageError("Unerwartete Antwort von Anthropic.")
    return parse_claude(data)


def color_for(provider, remaining, stale=False):
    if stale or remaining is None:
        return UNKNOWN_COLOR
    if remaining <= 10:
        return CRITICAL_COLOR
    if remaining <= 25:
        return WARNING_COLOR
    return COLORS[provider]


def percent_label(value):
    if value is None:
        return "–"
    # Do not claim zero/full when a fraction still remains/is already used.
    if 0 < value < 1:
        return "<1%"
    if 99 < value < 100:
        return "99%"
    return f"{math.floor(value + 0.5):.0f}%"


def tray_svg(states, now=None):
    """Wide GNOME AppIndicator artwork, with true proportional battery fills."""
    elements = []
    for index, (provider, letter) in enumerate((("claude", "A"), ("codex", "O"))):
        state = states[provider]
        value = state.snapshot.weekly.remaining if state.snapshot else None
        stale = state.stale(now)
        color = color_for(provider, value, stale)
        width = 65 * (value / 100) if value is not None else 0
        label = percent_label(value)
        if value is not None and stale:
            label = "~" + label
        # XML escaping matters for the <1% label.
        label = label.replace("<", "&lt;")
        x = index * 79
        elements.append(f'''<g transform="translate({x},0)">
<defs><clipPath id="clip{index}"><rect x="2" y="2" width="65" height="16" rx="3"/></clipPath></defs>
<rect x="1" y="1" width="67" height="18" rx="4" fill="#252A33" stroke="{color}" stroke-width="1.4"/>
<rect x="68" y="6" width="3" height="8" rx="1" fill="{color}"/>
<rect x="2" y="2" width="{width:.3f}" height="16" fill="{color}" fill-opacity="0.75" clip-path="url(#clip{index})"/>
<text x="8" y="14" font-family="DejaVu Sans,sans-serif" font-weight="bold" font-size="12" fill="#FFFFFF" stroke="#151A22" stroke-width="0.35" paint-order="stroke">{letter}</text>
<text x="62" y="14" text-anchor="end" font-family="DejaVu Sans,sans-serif" font-weight="bold" font-size="11" fill="#FFFFFF" stroke="#151A22" stroke-width="0.35" paint-order="stroke">{label}</text>
</g>''')
    return '<svg xmlns="http://www.w3.org/2000/svg" width="150" height="20" viewBox="0 0 150 20">' + ''.join(elements) + '</svg>'

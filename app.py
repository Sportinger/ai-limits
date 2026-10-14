#!/usr/bin/python3
"""Two weekly subscription batteries in the desktop system tray."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

from limits import (ProviderState, Snapshot, UsageError, Window, fetch_claude,
                    fetch_codex, percent_label, tray_svg)

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ai-limits"
FETCHERS = {"claude": fetch_claude, "codex": fetch_codex}
NAMES = {"claude": "A · Anthropic / Claude Code", "codex": "O · OpenAI / Codex"}


def ensure_cache():
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    CACHE.chmod(0o700)


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.chmod(0o600)
    temporary.replace(path)


def reset_label(timestamp):
    if timestamp is None:
        return "Reset-Zeit unbekannt"
    return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y, %H:%M")


def load_gtk():
    typelib_paths = [str(directory) for directory in (ROOT / "vendor/usr/lib").glob("*/girepository-1.0")]
    if typelib_paths:
        os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(typelib_paths + [os.environ.get("GI_TYPELIB_PATH", "")])
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import Gtk, GLib, AyatanaAppIndicator3
    return Gtk, GLib, AyatanaAppIndicator3


class TrayApp:
    def __init__(self, gtk, glib, indicator_lib):
        self.Gtk, self.GLib = gtk, glib
        self.states = {name: ProviderState() for name in FETCHERS}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-limits")
        self.closed = False
        self.icon_number = time.time_ns()
        self.last_artwork = None
        self.load_cache()
        self.indicator = indicator_lib.Indicator.new(
            "ai-limits", "battery-good-symbolic", indicator_lib.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_title("AI Limits · Wochenkontingent übrig")
        self.indicator.set_status(indicator_lib.IndicatorStatus.ACTIVE)
        self.menu = gtk.Menu()
        self.items = {}
        self.add_item("Wochenkontingent übrig")
        self.menu.append(gtk.SeparatorMenuItem())
        for provider in FETCHERS:
            self.add_item(NAMES[provider])
            self.items[provider] = {}
            for name in ("weekly", "reset", "session", "status", "extra"):
                self.items[provider][name] = self.add_item("Wird geladen …")
            self.menu.append(gtk.SeparatorMenuItem())
        self.refresh_item = self.add_item("Jetzt aktualisieren", self.refresh)
        self.add_item("Claude-Nutzungsseite öffnen", lambda *_: self.open_url("https://claude.ai/settings/usage"))
        self.add_item("Codex-Nutzungsseite öffnen", lambda *_: self.open_url("https://chatgpt.com/codex/settings/usage"))
        self.menu.append(gtk.SeparatorMenuItem())
        self.add_item("≤ 25 % dunkelorange · ≤ 10 % rot")
        self.add_item("Automatische Abfrage jede Minute")
        self.add_item("Beenden", self.quit)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        self.menu.connect("show", lambda *_: self.render())
        self.render()
        self.tick()
        glib.timeout_add_seconds(5, self.tick)
        from gi.repository import GLibUnix
        GLibUnix.signal_add(glib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        GLibUnix.signal_add(glib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)

    def add_item(self, text, callback=None):
        item = self.Gtk.MenuItem(label=text)
        item.set_sensitive(callback is not None)
        if callback:
            item.connect("activate", callback)
        self.menu.append(item)
        return item

    def open_url(self, url):
        from gi.repository import Gio
        Gio.AppInfo.launch_default_for_uri(url, None)

    def load_cache(self):
        try:
            data = json.loads((CACHE / "state.json").read_text())
            for name, state in self.states.items():
                raw = data.get(name, {}).get("snapshot")
                if raw and time.time() - raw["fetched_at"] < 86400:
                    # Cached values are always marked old until verified live.
                    state.snapshot = Snapshot(Window(**raw["weekly"]),
                        Window(**raw["session"]) if raw.get("session") else None,
                        bool(raw.get("extra_usage")), raw["fetched_at"])
                    state.error = "Gespeicherter Wert – Live-Abfrage läuft …"
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def save_cache(self):
        data = {name: {"snapshot": state.snapshot.to_dict() if state.snapshot else None,
                       "error": state.error, "stale": state.stale()}
                for name, state in self.states.items()}
        atomic_text(CACHE / "state.json", json.dumps(data, ensure_ascii=False, indent=2))

    def tick(self):
        if self.closed:
            return False
        for name, state in self.states.items():
            if not state.busy and time.monotonic() >= state.next_poll:
                state.busy = True
                future = self.executor.submit(FETCHERS[name])
                future.add_done_callback(lambda done, provider=name: self.GLib.idle_add(self.completed, provider, done))
        self.render()
        return True

    def completed(self, name, future):
        if self.closed:
            return False
        state = self.states[name]
        state.busy = False
        try:
            state.success(future.result())
        except UsageError as error:
            state.failure(error)
        except Exception as error:
            # Log only the type, never exception strings/headers/credentials.
            print(f"{name}: {type(error).__name__}", file=sys.stderr, flush=True)
            state.failure(UsageError("Abfrage fehlgeschlagen; erneuter Versuch folgt."))
        self.save_cache()
        self.render()
        return False

    def refresh(self, *_):
        # A click may refresh successful readings; it must not defeat backoff.
        for state in self.states.values():
            if not state.error:
                state.next_poll = 0
        self.tick()

    def render(self):
        artwork = tray_svg(self.states)
        if artwork != self.last_artwork:
            self.icon_number += 1
            path = CACHE / f"batteries-{self.icon_number}.svg"
            atomic_text(path, artwork)
            self.indicator.set_icon_full(str(path), "Verbleibendes Wochenkontingent: A Anthropic, O OpenAI")
            self.last_artwork = artwork
            # New names avoid the panel's icon cache. Keep a few old generations
            # so asynchronous icon readers can finish before files disappear.
            for old in CACHE.glob("batteries-*.svg"):
                if old != path and time.time() - old.stat().st_mtime > 120:
                    old.unlink(missing_ok=True)
        for name, state in self.states.items():
            items = self.items[name]
            snapshot = state.snapshot
            if snapshot:
                prefix = "Letzter Wert: " if state.stale() else "Woche: "
                items["weekly"].set_label(prefix + percent_label(snapshot.weekly.remaining) + " übrig")
                items["reset"].set_label("Wochenreset: " + reset_label(snapshot.weekly.resets_at))
                session = snapshot.session
                if session:
                    if session.resets_at and session.resets_at <= time.time():
                        items["session"].set_label("5 Stunden: Reset erreicht; noch kein neuer Wert")
                    else:
                        items["session"].set_label("5 Stunden: " + percent_label(session.remaining) + " übrig · Reset " + reset_label(session.resets_at))
                items["session"].set_visible(session is not None)
                items["extra"].set_label("Zusatznutzung verfügbar; 0 % = Wochenkontingent leer")
                items["extra"].set_visible(snapshot.extra_usage)
                stamp = datetime.fromtimestamp(snapshot.fetched_at).strftime("%H:%M:%S")
                message = f"Stand {stamp}" + (" · wird aktualisiert …" if state.busy else "")
                if state.stale() and not state.error:
                    message += " · veraltet, erneute Abfrage folgt"
            else:
                items["weekly"].set_label("Wochenkontingent: noch kein Wert")
                items["reset"].set_label("Wochenreset: noch unbekannt")
                items["session"].hide()
                items["extra"].hide()
                message = "Wird abgefragt …"
            if state.error:
                seconds = max(0, int(state.next_poll - time.monotonic()))
                message = state.error + (f" (in {seconds}s)" if seconds else "")
            items["status"].set_label(message)
        self.refresh_item.set_sensitive(not all(state.busy for state in self.states.values()))

    def quit(self, *_):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.Gtk.main_quit()
        return False


def main():
    parser = argparse.ArgumentParser(description="AI Limits: verbleibende Wochenkontingente in der Systemleiste")
    parser.add_argument("--check", action="store_true", help="Beide Anbieter einmal live abfragen und JSON ausgeben")
    parser.add_argument("--status", action="store_true", help="Zuletzt gespeicherte Werte anzeigen (keine Abfrage)")
    args = parser.parse_args()
    if args.check:
        output = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {name: executor.submit(fetch) for name, fetch in FETCHERS.items()}
            for name, future in futures.items():
                try:
                    output[name] = future.result().to_dict()
                except UsageError as error:
                    output[name] = {"error": str(error)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return int(any("error" in value for value in output.values()))
    if args.status:
        try:
            print((CACHE / "state.json").read_text())
            return 0
        except OSError:
            print("AI Limits hat noch keine Werte gespeichert.")
            return 1
    ensure_cache()
    # Desktop launcher + autostart + manual invocation share one instance.
    lock = (CACHE / "app.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    gtk, glib, indicator_lib = load_gtk()
    if not gtk.init_check()[0]:
        print("Keine grafische Desktop-Sitzung gefunden.", file=sys.stderr)
        return 1
    app = TrayApp(gtk, glib, indicator_lib)
    gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

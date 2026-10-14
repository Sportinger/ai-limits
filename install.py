#!/usr/bin/python3
"""Install a checkout for the current desktop user; no root access required."""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import shutil
import subprocess


def desktop_quote(value):
    # Desktop Entry Exec syntax is not shell syntax.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def installation_files(root, home):
    root, home = Path(root).resolve(), Path(home).resolve()
    if any(character in str(path) for path in (root, home) for character in ('\n', '\r')):
        raise ValueError("Installation paths must not contain line breaks.")
    launcher = home / ".local/bin/ai-limits"
    service = home / ".config/systemd/user/ai-limits.service"
    desktop = home / ".local/share/applications/ai-limits.desktop"
    return {
        launcher: (f'''#!/bin/sh
if [ "$#" -gt 0 ]; then
    exec /usr/bin/python3 {shlex.quote(str(root / 'app.py'))} "$@"
fi
exec systemctl --user start ai-limits.service
''', 0o755),
        service: (f'''[Unit]
Description=AI Limits – Claude and Codex weekly allowances
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {unit_quote(root / 'app.py')}
WorkingDirectory={str(root).replace('%', '%%')}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=15
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=graphical-session.target
''', 0o644),
        desktop: (f'''[Desktop Entry]
Version=1.0
Type=Application
Name=AI Limits
Comment=Remaining weekly allowances for Claude Code and Codex
Comment[de]=Verbleibende Wochenkontingente von Claude Code und Codex
Exec={desktop_quote(launcher)}
Icon={root / 'icon.svg'}
Terminal=false
Categories=System;Monitor;
Keywords=Claude;Codex;Anthropic;OpenAI;Usage;Quota;Wochenlimit;Tokens;
StartupNotify=false
''', 0o644),
    }


def install(root, home, start=True):
    for path, (content, mode) in installation_files(root, home).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(mode)
    desktop = Path(home) / ".local/share/applications/ai-limits.desktop"
    subprocess.run(["desktop-file-validate", str(desktop)], check=True)
    if start:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "ai-limits.service"], check=True)
    print("AI Limits installed" + (" and started." if start else "; service activation skipped."))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-start", action="store_true", help="Install files without enabling or starting the service")
    args = parser.parse_args()
    for command in ("desktop-file-validate", "systemctl"):
        if not shutil.which(command):
            parser.exit(1, f"Missing {command}. See the requirements in README.md.\n")
    from app import load_gtk
    try:
        load_gtk()
    except (ImportError, ValueError):
        parser.exit(1, "Missing GTK/AppIndicator dependencies. On Ubuntu:\n"
                    "sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 desktop-file-utils\n")
    install(Path(__file__).resolve().parent, Path.home(), start=not args.no_start)


if __name__ == "__main__":
    main()

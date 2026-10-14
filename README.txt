AI Limits
=========

Eine kleine GNOME-Systemleisten-App für verbleibende Wochenkontingente.
A = Anthropic / Claude Code (orange), O = OpenAI / Codex (grün).
100 % = Wochenkontingent voll, 0 % = Wochenkontingent aufgebraucht.
Bei höchstens 25 % wird der Balken dunkelorange, bei höchstens 10 % rot.
Graue Balken mit ~ zeigen einen veralteten Wert; – bedeutet noch unbekannt.

Bedienung
---------
Anwendungsmenü: AI Limits. Klick auf die Batterien öffnet die Details.
Automatische Aktualisierung jede Minute. Bei Netzfehlern oder HTTP 429 wartet
die App zunehmend länger und beachtet Retry-After. „Beenden“ schließt die App
bis zum nächsten Start. Der Autostart läuft als systemd-Benutzerdienst:
~/.config/systemd/user/ai-limits.service.

Bei abgelaufener Claude-Anmeldung Claude Code öffnen bzw. dort neu anmelden.
Die App liest die aktuelle Anmeldung bei jeder Abfrage erneut ein und schreibt
nicht in die Anmeldedatei. Codex verwaltet seine Anmeldung über die offizielle CLI.

Zusatznutzung/Guthaben kann nach leerem Wochenkontingent weitere Arbeit erlauben.
Die Balken messen ausschließlich das allgemeine Wochenkontingent. Weitere Limits
(zum Beispiel 5 Stunden oder bestimmte Modelle) können unabhängig davon greifen.
Die Werte sind Kontingent-Prozente, keine Schätzung verbleibender Tokenzahlen.

Technik und Prüfung
------------------
/usr/bin/python3 app.py --check   Beide Anbieter einmal live auslesen
/usr/bin/python3 app.py --status  Zuletzt gespeicherte Werte anzeigen
/usr/bin/python3 -m unittest -v  Tests für Parser, Grenzen und Fehlerfälle
/usr/bin/python3 install.py     Starter und Autostart installieren/aktualisieren
systemctl --user stop ai-limits Sofort beenden
systemctl --user disable --now ai-limits  Autostart abschalten und beenden

OpenAI: codex app-server, initialize + account/rateLimits/read über stdio.
https://learn.chatgpt.com/docs/app-server
Anthropic: GET https://api.anthropic.com/api/oauth/usage mit lokaler Claude-Code-
OAuth-Anmeldung. Diese Schnittstelle ist nicht als stabile öffentliche API
zugesichert. Dokumentation der offiziellen Statuszeilenfelder:
https://code.claude.com/docs/en/statusline

Es werden keine Modellanfragen ausgeführt. Keine API-Schlüssel erforderlich.
Anmeldedaten werden weder kopiert noch protokolliert. Nur normalisierte
Prozentwerte und Reset-Zeiten liegen in ~/.cache/ai-limits (privater Ordner).
Die OAuth-Anfrage folgt keinen Weiterleitungen. Es werden keine Reset-Guthaben
verbraucht und keine Zusatzkäufe ausgelöst.

Abhängigkeiten: System-Python mit PyGObject, GTK 3 und libayatana-appindicator3.
Fehlende GI-Typinformationen liegen unter vendor/ aus offiziellen Ubuntu-Paketen.
Keine systemweiten Paketänderungen nötig. Die zugehörigen Paket-Lizenzen liegen
in vendor/usr/share/doc.

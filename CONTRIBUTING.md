# Contributing

Bug reports, fixes, desktop compatibility notes, and translations are welcome.

For bug reports, include your distribution, GNOME version, CLI versions, and the error shown in the menu. Remove account identifiers, local paths you consider private, and credentials from anything you share. Never attach CLI authentication files.

Run the offline tests before submitting a change:

```bash
python3 -m unittest discover -v
```

Check tray rendering and the menu in a desktop session when changing the UI. Provider tests should use fixtures or mocks; CI must not require a subscription, authenticate to a real account, or send model prompts. Preserve explicit handling of unknown and stale values.

Contributions are made under this project's MIT license.

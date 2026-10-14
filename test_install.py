from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from install import install, installation_files


class InstallTests(unittest.TestCase):
    def test_launcher_preserves_spaces_and_literal_shell_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "a space $USER `nope` quote'"
            source.mkdir()
            (source / "app.py").write_text("import sys; print(sys.argv[1])")
            home = base / "home space"
            launcher = home / ".local/bin/ai-limits"
            text, mode = installation_files(source, home)[launcher]
            launcher.parent.mkdir(parents=True)
            launcher.write_text(text)
            launcher.chmod(mode)
            result = subprocess.run([str(launcher), "literal $HOME `echo bad`"], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "literal $HOME `echo bad`")

    def test_no_start_installs_only_files_and_preserves_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            secret = home / ".claude/.credentials.json"
            secret.parent.mkdir()
            secret.write_text('private test fixture')
            with patch("install.subprocess.run") as run:
                install(home / "project", home, start=False)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "desktop-file-validate")
            self.assertTrue((home / ".config/systemd/user/ai-limits.service").is_file())
            self.assertEqual(secret.read_text(), 'private test fixture')

    def test_systemd_specifiers_are_literal(self):
        files = installation_files(Path('/tmp/app %n'), Path('/tmp/home'))
        service = files[Path('/tmp/home/.config/systemd/user/ai-limits.service')][0]
        self.assertIn('/tmp/app %%n/app.py', service)

    def test_line_breaks_cannot_inject_service_directives(self):
        with self.assertRaises(ValueError):
            installation_files('/tmp/app\nExecStart=bad', '/tmp/home')


if __name__ == '__main__':
    unittest.main()

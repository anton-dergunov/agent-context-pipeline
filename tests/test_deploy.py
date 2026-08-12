import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPOSITORY / "deploy.sh"
STUB = """#!/bin/sh
command_name="$(basename "$0")"
printf '%s' "$command_name" >>"$COMMAND_LOG"
for argument in "$@"; do
    printf '\t%s' "$argument" >>"$COMMAND_LOG"
done
printf '\n' >>"$COMMAND_LOG"
if [ "${FAIL_COMMAND:-}" = "$command_name" ]; then
    exit 23
fi
"""


class DeployScriptTests(unittest.TestCase):
    def run_deploy(self, root: Path, *, fail_command: str = "") -> subprocess.CompletedProcess:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for command in ("ssh", "rsync", "curl", "sleep"):
            stub = bin_dir / command
            stub.write_text(STUB, encoding="utf-8")
            stub.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{bin_dir}:{environment['PATH']}",
                "COMMAND_LOG": str(root / "commands.log"),
                "FAIL_COMMAND": fail_command,
            }
        )
        return subprocess.run(
            ["/bin/bash", str(DEPLOY_SCRIPT)],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
        )

    @staticmethod
    def commands(root: Path) -> list[list[str]]:
        log = root / "commands.log"
        if not log.exists():
            return []
        return [line.split("\t") for line in log.read_text().splitlines()]

    def test_missing_env_stops_before_external_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_deploy(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing .env", result.stderr)
            self.assertEqual(self.commands(root), [])

    def test_happy_path_preserves_data_and_runs_health_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = "TELEGRAM_BOT_TOKEN=never-print-this"
            (root / ".env").write_text(secret, encoding="utf-8")

            result = self.run_deploy(root)
            commands = self.commands(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                [command[0] for command in commands],
                ["ssh", "rsync", "rsync", "ssh", "ssh", "sleep", "curl"],
            )
            mirror = commands[1]
            self.assertIn("--delete", mirror)
            self.assertIn("data/", mirror)
            self.assertIn(".env", mirror)
            self.assertEqual(mirror[-2:], ["./", "server:/volume1/docker/info-triage/"])
            self.assertEqual(commands[2][-2:], [".env", "server:/volume1/docker/info-triage/.env"])
            self.assertIn("chmod 600", commands[3][2])
            self.assertIn("deploy-container info-triage", commands[4][2])
            self.assertEqual(commands[5], ["sleep", "3"])
            self.assertEqual(commands[6][0:4], ["curl", "--fail", "--silent", "--show-error"])
            self.assertIn("Deployment complete", result.stdout)
            self.assertNotIn(
                secret, result.stdout + result.stderr + (root / "commands.log").read_text()
            )

    def test_failed_rsync_stops_deployment_immediately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")

            result = self.run_deploy(root, fail_command="rsync")

            self.assertEqual(result.returncode, 23)
            self.assertEqual([command[0] for command in self.commands(root)], ["ssh", "rsync"])
            self.assertNotIn("Deployment complete", result.stdout)

    def test_failed_health_check_does_not_report_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")

            result = self.run_deploy(root, fail_command="curl")

            self.assertEqual(result.returncode, 23)
            self.assertEqual(self.commands(root)[-1][0], "curl")
            self.assertNotIn("Deployment complete", result.stdout)


if __name__ == "__main__":
    unittest.main()

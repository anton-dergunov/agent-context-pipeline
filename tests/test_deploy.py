import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
STUB = """#!/bin/sh
command_name="$(basename "$0")"
printf '%s' "$command_name" >>"$COMMAND_LOG"
for argument in "$@"; do
    printf '\\t%s' "$argument" >>"$COMMAND_LOG"
done
printf '\\n' >>"$COMMAND_LOG"
if [ "${FAIL_COMMAND:-}" = "$command_name" ]; then
    exit 23
fi
if [ "$command_name" = "ssh" ] && [ "${1:-}" = "-G" ]; then
    printf 'user someone\\nhostname server.test\\nport 22\\n'
fi
"""
SECRET = "TELEGRAM_BOT_TOKEN_INFO=never-print-this"
REMOTE_ENV = (
    f"{SECRET}\n"
    "INFO_TRIAGE_SERVER=box\n"
    "INFO_TRIAGE_SERVER_DIR=/srv/info-triage\n"
)
CURL = [
    "curl",
    "--fail",
    "--silent",
    "--connect-timeout",
    "2",
    "--max-time",
    "5",
    "--retry",
    "60",
    "--retry-all-errors",
    "--retry-delay",
    "1",
    "--retry-max-time",
    "60",
]


class DeployScriptTests(unittest.TestCase):
    def checkout(self, env: str | None = REMOTE_ENV, *, config: bool = True) -> Path:
        """A directory holding the script and the two local files it requires."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        shutil.copy2(REPOSITORY / "deploy.sh", root / "deploy.sh")
        if env is not None:
            (root / ".env").write_text(env, encoding="utf-8")
        if config:
            (root / "config.yaml").write_text("routes: []\n", encoding="utf-8")
        return root

    def run_deploy(self, root: Path, *, fail_command: str = "") -> subprocess.CompletedProcess:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for command in ("ssh", "rsync", "curl", "docker"):
            stub = bin_dir / command
            stub.write_text(STUB, encoding="utf-8")
            stub.chmod(0o755)
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("INFO_TRIAGE_")
        }
        environment.update(
            {
                "PATH": f"{bin_dir}:{environment['PATH']}",
                "COMMAND_LOG": str(root / "commands.log"),
                "FAIL_COMMAND": fail_command,
            }
        )
        return subprocess.run(
            ["/bin/bash", str(root / "deploy.sh")],
            # Somewhere else on purpose: the script has to find its own directory.
            cwd=root.parent,
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
        root = self.checkout(env=None)
        result = self.run_deploy(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing .env", result.stderr)
        self.assertEqual(self.commands(root), [])

    def test_missing_config_stops_before_external_commands(self):
        root = self.checkout(config=False)
        result = self.run_deploy(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("copy config.example.yaml to config.yaml", result.stderr)
        self.assertEqual(self.commands(root), [])

    def test_a_server_without_its_directory_stops_before_external_commands(self):
        root = self.checkout(env="INFO_TRIAGE_SERVER=box\n")
        result = self.run_deploy(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INFO_TRIAGE_SERVER_DIR is not set", result.stderr)
        self.assertEqual(self.commands(root), [])

    def test_happy_path_preserves_data_and_runs_health_check(self):
        root = self.checkout()

        result = self.run_deploy(root)
        commands = self.commands(root)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [command[0] for command in commands],
            ["ssh", "rsync", "rsync", "ssh", "ssh", "ssh", "curl"],
        )
        self.assertEqual(commands[0], ["ssh", "box", "mkdir -p '/srv/info-triage/data'"])
        mirror = commands[1]
        self.assertIn("--delete", mirror)
        self.assertNotIn("--delete-excluded", mirror)
        self.assertIn("data/", mirror)
        self.assertIn(".env", mirror)
        self.assertEqual(mirror[-2:], ["./", "box:/srv/info-triage/"])
        self.assertEqual(commands[2][-2:], [".env", "box:/srv/info-triage/.env"])
        self.assertIn("chmod 600", commands[3][2])
        # No command named, so the stock one runs in the project directory.
        self.assertEqual(
            commands[4], ["ssh", "box", "cd '/srv/info-triage' && docker compose up -d --build"]
        )
        self.assertEqual(commands[5], ["ssh", "-G", "box"])
        self.assertEqual(commands[6], [*CURL, "http://server.test:8000/health"])
        self.assertIn("Deployment complete", result.stdout)
        self.assertNotIn(
            SECRET, result.stdout + result.stderr + (root / "commands.log").read_text()
        )

    def test_a_configured_deploy_command_replaces_the_stock_one(self):
        """A server that grants one passwordless command, not Docker itself."""
        root = self.checkout(
            REMOTE_ENV + "INFO_TRIAGE_DEPLOY_COMMAND='sudo -n /usr/local/sbin/deploy-container x'\n"
        )

        result = self.run_deploy(root)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.commands(root)[4], ["ssh", "box", "sudo -n /usr/local/sbin/deploy-container x"]
        )

    def test_without_a_server_the_container_is_started_on_this_machine(self):
        root = self.checkout(env=SECRET + "\n")

        result = self.run_deploy(root)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.commands(root),
            [
                ["docker", "compose", "up", "-d", "--build"],
                [*CURL, "http://localhost:8000/health"],
            ],
        )
        self.assertTrue((root / "data").is_dir())

    def test_failed_rsync_stops_deployment_immediately(self):
        root = self.checkout()

        result = self.run_deploy(root, fail_command="rsync")

        self.assertEqual(result.returncode, 23)
        self.assertEqual([command[0] for command in self.commands(root)], ["ssh", "rsync"])
        self.assertNotIn("Deployment complete", result.stdout)

    def test_failed_health_check_does_not_report_success(self):
        root = self.checkout()

        result = self.run_deploy(root, fail_command="curl")

        self.assertEqual(result.returncode, 23)
        self.assertEqual(self.commands(root)[-1][0], "curl")
        self.assertIn("did not answer at http://server.test:8000/health", result.stderr)
        self.assertNotIn("Deployment complete", result.stdout)


if __name__ == "__main__":
    unittest.main()

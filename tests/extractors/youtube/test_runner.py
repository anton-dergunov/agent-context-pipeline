import hashlib
import json
import subprocess

import pytest

from info_triage.extractors.youtube.runner import (
    ManagedYtDlp,
    RunnerSettings,
    YtDlpError,
    classify_failure,
)


class Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_verified_update_is_atomic_and_check_interval_is_persisted(tmp_path):
    payload = b"official executable"
    checksum = hashlib.sha256(payload).hexdigest().encode()
    downloads = []

    def get(url, **_kwargs):
        downloads.append(url)
        return Response(checksum + b"  yt-dlp\n" if url.endswith("SHA2-256SUMS") else payload)

    def run(command, **_kwargs):
        assert command[-1] == "--version"
        return completed(stdout="2026.8.4.234419.dev0\n")

    runner = ManagedYtDlp(
        RunnerSettings(tool_dir=tmp_path, update_check_interval_hours=24),
        process_runner=run,
        http_get=get,
        now=lambda: 1_000,
    )
    assert runner.update()
    assert (tmp_path / "yt-dlp").read_bytes() == payload
    assert not runner.update()
    assert len(downloads) == 2
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["active_version"] == "2026.8.4.234419.dev0"


def test_checksum_failure_preserves_active_executable(tmp_path):
    executable = tmp_path / "yt-dlp"
    executable.write_bytes(b"known good")
    (tmp_path / "state.json").write_text(json.dumps({"active_version": "old"}), encoding="utf-8")

    def get(url, **_kwargs):
        return Response(b"0" * 64 + b"  yt-dlp\n" if url.endswith("SHA2-256SUMS") else b"bad")

    runner = ManagedYtDlp(
        RunnerSettings(tool_dir=tmp_path),
        process_runner=lambda *_args, **_kwargs: completed(stdout="old\n"),
        http_get=get,
        now=lambda: 1_000,
    )
    assert not runner.update(force=True)
    assert executable.read_bytes() == b"known good"
    assert runner.events[-1]["action"] == "update_check_failed"


def test_compatibility_failure_forces_update_and_retries(tmp_path):
    executable = tmp_path / "yt-dlp"
    executable.write_bytes(b"old")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "active_version": "old",
                "last_successful_version": "old",
                "last_checked_at": 1_000,
            }
        ),
        encoding="utf-8",
    )
    payload = b"new"
    checksum = hashlib.sha256(payload).hexdigest().encode()
    commands = 0

    def get(url, **_kwargs):
        return Response(checksum + b"  yt-dlp\n" if url.endswith("SHA2-256SUMS") else payload)

    def run(command, **_kwargs):
        nonlocal commands
        if command[-1] == "--version":
            if ".part" in command[0]:
                return completed(stdout="new\n")
            return completed(stdout="old\n")
        commands += 1
        if commands == 1:
            return completed(stderr="ERROR: signature extraction failed", returncode=1)
        return completed(stdout='{"id":"abc"}\n')

    runner = ManagedYtDlp(
        RunnerSettings(tool_dir=tmp_path, max_attempts=3),
        process_runner=run,
        http_get=get,
        now=lambda: 1_001,
        sleeper=lambda _seconds: None,
    )
    result = runner.run_json(["--dump-single-json"])
    assert result["id"] == "abc"
    assert commands == 2
    assert any(event["action"] == "updated" for event in runner.events)


def test_failed_new_version_rolls_back_to_last_known_good(tmp_path):
    executable = tmp_path / "yt-dlp"
    executable.write_bytes(b"old")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "active_version": "old",
                "last_successful_version": "old",
                "last_checked_at": 0,
            }
        ),
        encoding="utf-8",
    )
    payload = b"new"
    checksum = hashlib.sha256(payload).hexdigest().encode()

    def get(url, **_kwargs):
        return Response(checksum + b"  yt-dlp\n" if url.endswith("SHA2-256SUMS") else payload)

    def run(command, **_kwargs):
        if command[-1] == "--version":
            return completed(stdout="new\n" if ".part" in command[0] else "old\n")
        if command[0].endswith("yt-dlp.previous"):
            return completed(stdout="ok")
        return completed(stderr="unexpected extractor regression", returncode=1)

    runner = ManagedYtDlp(
        RunnerSettings(tool_dir=tmp_path),
        process_runner=run,
        http_get=get,
        now=lambda: 100_000,
        sleeper=lambda _seconds: None,
    )
    assert runner.run(["URL"]).stdout == "ok"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["active_version"] == "old"
    assert runner.events[-1]["action"] == "rolled_back"


def test_failure_classification_separates_access_network_and_compatibility():
    assert classify_failure("ERROR: Private video") == "non_retryable"
    assert classify_failure("HTTP Error 429: Too Many Requests") == "transient"
    assert classify_failure("Unable to extract player response") == "compatibility"


def test_offline_update_check_warns_and_uses_pinned_module(tmp_path):
    def get(*_args, **_kwargs):
        raise OSError("offline")

    def run(command, **_kwargs):
        if command[-1] == "--version":
            return completed(stdout="pinned\n")
        return completed(stdout='{"id":"abc"}\n')

    runner = ManagedYtDlp(
        RunnerSettings(tool_dir=tmp_path),
        process_runner=run,
        http_get=get,
        now=lambda: 1_000,
    )
    assert runner.run_json(["--dump-single-json"])["id"] == "abc"
    assert runner.events[0]["action"] == "update_check_failed"


def test_transient_failures_use_exponential_backoff(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"last_checked_at": 1_000}), encoding="utf-8")
    attempts = 0
    waits = []

    def run(command, **_kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return completed(stdout="pinned\n")
        attempts += 1
        if attempts < 3:
            return completed(stderr="HTTP Error 429: throttled", returncode=1)
        return completed(stdout="ok")

    runner = ManagedYtDlp(
        RunnerSettings(
            tool_dir=tmp_path,
            max_attempts=3,
            retry_backoff_seconds=5,
        ),
        process_runner=run,
        now=lambda: 1_001,
        sleeper=waits.append,
    )
    assert runner.run(["URL"]).stdout == "ok"
    assert attempts == 3
    assert waits == [5, 10]


def test_unknown_failure_is_not_retried(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"last_checked_at": 1_000}), encoding="utf-8")
    attempts = 0

    def run(command, **_kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return completed(stdout="pinned\n")
        attempts += 1
        return completed(stderr="an unclassified fatal error", returncode=1)

    runner = ManagedYtDlp(
        RunnerSettings(tool_dir=tmp_path, max_attempts=3),
        process_runner=run,
        now=lambda: 1_001,
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(YtDlpError):
        runner.run(["URL"])
    assert attempts == 1

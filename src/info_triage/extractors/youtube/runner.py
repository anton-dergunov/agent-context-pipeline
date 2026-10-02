"""Isolated yt-dlp execution with verified hot updates and bounded retries."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

CHANNEL_REPOSITORIES = {
    "stable": "yt-dlp/yt-dlp",
    "nightly": "yt-dlp/yt-dlp-nightly-builds",
    "master": "yt-dlp/yt-dlp-master-builds",
}

NON_RETRYABLE_MARKERS = (
    "private video",
    "members-only",
    "members only",
    "video unavailable",
    "this video is unavailable",
    "not available in your country",
    "geo-restricted",
    "copyright",
    "login required",
    "sign in to confirm your age",
    "sign in to confirm you’re not a bot",
    "sign in to confirm you're not a bot",
    "authentication required",
    "unsupported url",
    "is not a valid url",
)
TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "remote end closed",
    "temporary failure",
    "temporarily unavailable",
    "failed to resolve",
    "name or service not known",
    "http error 429",
    "too many requests",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
)
COMPATIBILITY_MARKERS = (
    "unable to extract",
    "signature extraction failed",
    "nsig extraction failed",
    "n challenge solving failed",
    "challenge solving failed",
    "unable to download api page",
    "no video formats found",
)


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    tool_dir: Path
    channel: str = "nightly"
    update_check_interval_hours: float = 24.0
    update_on_compatibility_error: bool = True
    max_attempts: int = 3
    retry_backoff_seconds: float = 5.0
    update_timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int
    version: str


class YtDlpError(RuntimeError):
    def __init__(self, message: str, *, kind: str, result: CommandResult | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.result = result


def classify_failure(stderr: str) -> str:
    message = stderr.casefold()
    if any(marker in message for marker in NON_RETRYABLE_MARKERS):
        return "non_retryable"
    if any(marker in message for marker in TRANSIENT_MARKERS):
        return "transient"
    if any(marker in message for marker in COMPATIBILITY_MARKERS):
        return "compatibility"
    return "unknown"


class ManagedYtDlp:
    """Run yt-dlp out of process so a replacement is visible immediately."""

    def __init__(
        self,
        settings: RunnerSettings,
        *,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        http_get: Callable[..., Any] = requests.get,
        now: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.channel not in CHANNEL_REPOSITORIES:
            raise ValueError(f"unsupported yt-dlp update channel: {settings.channel}")
        if settings.max_attempts < 1:
            raise ValueError("yt-dlp max attempts must be positive")
        self.settings = settings
        self._run_process = process_runner
        self._http_get = http_get
        self._now = now
        self._sleep = sleeper
        self.executable = settings.tool_dir / "yt-dlp"
        self.previous_executable = settings.tool_dir / "yt-dlp.previous"
        self.state_path = settings.tool_dir / "state.json"
        self.events: list[dict[str, Any]] = []
        self._baseline_version: str | None = None

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, value: dict[str, Any]) -> None:
        self.settings.tool_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.settings.tool_dir,
            prefix="state.",
            suffix=".part",
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, self.state_path)

    def _command(self, executable: Path | None = None) -> list[str]:
        if executable is not None:
            return [str(executable)]
        if self.executable.is_file():
            return [str(self.executable)]
        return [sys.executable, "-m", "yt_dlp"]

    def _invoke(
        self,
        arguments: Sequence[str],
        *,
        executable: Path | None = None,
    ) -> CommandResult:
        command = [*self._command(executable), *arguments]
        completed = self._run_process(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            version=self.current_version(executable),
        )

    def current_version(self, executable: Path | None = None) -> str:
        if executable is None and self.executable.is_file():
            state_version = self._read_state().get("active_version")
            if isinstance(state_version, str) and state_version:
                return state_version
        if executable is None and self._baseline_version:
            return self._baseline_version
        completed = self._run_process(
            [*self._command(executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            return "unknown"
        version = completed.stdout.strip().splitlines()[-1]
        if executable is None and not self.executable.is_file():
            self._baseline_version = version
        return version

    def _release_url(self, asset: str) -> str:
        repository = CHANNEL_REPOSITORIES[self.settings.channel]
        return f"https://github.com/{repository}/releases/latest/download/{asset}"

    def _download(self, url: str) -> bytes:
        response = self._http_get(url, timeout=self.settings.update_timeout_seconds)
        response.raise_for_status()
        return bytes(response.content)

    @staticmethod
    def _expected_checksum(checksums: bytes) -> str:
        for raw_line in checksums.decode("utf-8").splitlines():
            parts = raw_line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == "yt-dlp":
                return parts[0].lower()
        raise RuntimeError("official yt-dlp checksum file has no yt-dlp entry")

    def _record_check_failure(self, error: Exception) -> None:
        state = self._read_state()
        state["last_checked_at"] = self._now()
        state["last_check_error"] = f"{type(error).__name__}: {error}"
        self._write_state(state)
        self.events.append({"action": "update_check_failed", "error": state["last_check_error"]})

    def update(self, *, force: bool = False) -> bool:
        """Install a verified candidate atomically; return whether its version changed."""
        state = self._read_state()
        now = self._now()
        last_checked = state.get("last_checked_at")
        interval = self.settings.update_check_interval_hours * 3600
        if not force and isinstance(last_checked, (int, float)) and now - last_checked < interval:
            return False

        self.settings.tool_dir.mkdir(parents=True, exist_ok=True)
        try:
            checksums = self._download(self._release_url("SHA2-256SUMS"))
            payload = self._download(self._release_url("yt-dlp"))
            expected = self._expected_checksum(checksums)
            actual = hashlib.sha256(payload).hexdigest()
            if actual != expected:
                raise RuntimeError(
                    f"yt-dlp checksum mismatch: expected {expected}, received {actual}"
                )

            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.settings.tool_dir,
                prefix="yt-dlp.",
                suffix=".part",
                delete=False,
            ) as handle:
                handle.write(payload)
                candidate = Path(handle.name)
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            candidate_version = self.current_version(candidate)
            if candidate_version == "unknown":
                candidate.unlink(missing_ok=True)
                raise RuntimeError("downloaded yt-dlp executable did not pass --version")

            old_version = self.current_version() if self.executable.exists() else None
            changed = old_version != candidate_version or not self.executable.exists()
            if changed:
                if self.executable.exists():
                    shutil.copy2(self.executable, self.previous_executable)
                os.replace(candidate, self.executable)
            else:
                candidate.unlink(missing_ok=True)

            state.update(
                {
                    "active_version": candidate_version,
                    "last_checked_at": now,
                    "last_check_error": None,
                }
            )
            if changed:
                state["previous_version"] = old_version
                state["updated_at"] = now
            self._write_state(state)
            self.events.append(
                {
                    "action": "updated" if changed else "already_current",
                    "version": candidate_version,
                    "channel": self.settings.channel,
                }
            )
            return changed
        except Exception as error:
            self._record_check_failure(error)
            return False

    def ensure_fresh(self) -> None:
        self.update(force=False)

    def _mark_success(self, version: str) -> None:
        state = self._read_state()
        state["last_successful_version"] = version
        self._write_state(state)

    def _can_try_previous(self, failed_version: str) -> bool:
        state = self._read_state()
        return (
            self.previous_executable.is_file()
            and state.get("previous_version") == state.get("last_successful_version")
            and state.get("previous_version") != failed_version
        )

    def _restore_previous(self, version: str) -> None:
        failed_path = self.settings.tool_dir / "yt-dlp.failed"
        if self.executable.exists():
            os.replace(self.executable, failed_path)
        os.replace(self.previous_executable, self.executable)
        failed_path.unlink(missing_ok=True)
        state = self._read_state()
        state.update(
            {
                "active_version": version,
                "previous_version": None,
                "rolled_back_at": self._now(),
            }
        )
        self._write_state(state)
        self.events.append({"action": "rolled_back", "version": version})

    def run(self, arguments: Sequence[str], *, check_updates: bool = True) -> CommandResult:
        if check_updates:
            self.ensure_fresh()
        forced_update = False
        last_result: CommandResult | None = None
        last_kind = "unknown"

        for attempt in range(self.settings.max_attempts):
            result = self._invoke(arguments)
            last_result = result
            if result.returncode == 0:
                self._mark_success(result.version)
                return result
            kind = classify_failure(result.stderr)
            last_kind = kind
            self.events.append(
                {
                    "action": "command_failed",
                    "attempt": attempt + 1,
                    "kind": kind,
                    "version": result.version,
                }
            )
            if kind == "non_retryable":
                break

            if self._can_try_previous(result.version):
                previous = self._invoke(arguments, executable=self.previous_executable)
                if previous.returncode == 0:
                    self._restore_previous(previous.version)
                    self._mark_success(previous.version)
                    return previous

            if (
                kind == "compatibility"
                and self.settings.update_on_compatibility_error
                and not forced_update
            ):
                self.update(force=True)
                forced_update = True

            if kind not in {"transient", "compatibility"}:
                break

            if attempt + 1 < self.settings.max_attempts:
                self._sleep(self.settings.retry_backoff_seconds * (2**attempt))

        detail = (last_result.stderr if last_result else "yt-dlp did not run").strip()
        if len(detail) > 2_000:
            detail = detail[-2_000:]
        raise YtDlpError(
            detail or "yt-dlp failed without an error message",
            kind=last_kind,
            result=last_result,
        )

    def run_json(self, arguments: Sequence[str], *, check_updates: bool = True) -> dict[str, Any]:
        result = self.run(arguments, check_updates=check_updates)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise YtDlpError(
                f"yt-dlp returned invalid JSON: {error}",
                kind="compatibility",
                result=result,
            ) from error
        if not isinstance(value, dict):
            raise YtDlpError(
                "yt-dlp returned a non-object JSON result",
                kind="compatibility",
                result=result,
            )
        return value

import platform

import pytest

from instagram_extractor import runtime


def test_explicit_request_wins(monkeypatch):
    monkeypatch.setenv(runtime.THREAD_ENV, "8")
    assert runtime.resolve_threads(3) == 3


def test_environment_is_used_when_no_request(monkeypatch):
    monkeypatch.setenv(runtime.THREAD_ENV, "5")
    assert runtime.resolve_threads() == 5


def test_invalid_environment_falls_through(monkeypatch):
    monkeypatch.setenv(runtime.THREAD_ENV, "not-a-number")
    monkeypatch.setattr(runtime, "cgroup_cpu_limit", lambda: 2.0)
    assert runtime.resolve_threads() == 2


def test_cgroup_limit_is_preferred_over_host_cpu_count(monkeypatch):
    """The whole point: a capped container must not size threads from the host."""
    monkeypatch.delenv(runtime.THREAD_ENV, raising=False)
    monkeypatch.setattr(runtime, "cgroup_cpu_limit", lambda: 1.5)
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: 32)
    assert runtime.resolve_threads() == 1


def test_falls_back_to_cpu_count_when_unlimited(monkeypatch):
    monkeypatch.delenv(runtime.THREAD_ENV, raising=False)
    monkeypatch.setattr(runtime, "cgroup_cpu_limit", lambda: None)
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: 4)
    assert runtime.resolve_threads() == 4


def test_cgroup_v2_quota(tmp_path, monkeypatch):
    path = tmp_path / "cpu.max"
    path.write_text("150000 100000\n")
    monkeypatch.setattr(runtime, "_CGROUP_V2", path)
    assert runtime.cgroup_cpu_limit() == pytest.approx(1.5)


def test_cgroup_v2_unlimited(tmp_path, monkeypatch):
    path = tmp_path / "cpu.max"
    path.write_text("max 100000\n")
    monkeypatch.setattr(runtime, "_CGROUP_V2", path)
    monkeypatch.setattr(runtime, "_CGROUP_V1_QUOTA", tmp_path / "missing")
    assert runtime.cgroup_cpu_limit() is None


def test_cgroup_v1_quota(tmp_path, monkeypatch):
    quota = tmp_path / "quota"
    period = tmp_path / "period"
    quota.write_text("200000")
    period.write_text("100000")
    monkeypatch.setattr(runtime, "_CGROUP_V2", tmp_path / "missing")
    monkeypatch.setattr(runtime, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(runtime, "_CGROUP_V1_PERIOD", period)
    assert runtime.cgroup_cpu_limit() == pytest.approx(2.0)


def test_platform_defaults_never_pick_vision_off_macos(monkeypatch):
    """`best` used to resolve to Apple Vision everywhere, which cannot load on Linux."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert runtime.platform_defaults() == ("rapidocr", "rapidocr")

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert runtime.platform_defaults() == ("surya", "vision")

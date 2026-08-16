"""Tests for shared media runtime resource selection."""

import platform

import pytest

from info_triage.extractors.media import runtime


def test_explicit_request_wins(monkeypatch):
    monkeypatch.setenv(runtime.THREAD_ENV, "8")
    assert runtime.resolve_threads(3) == 3


def test_environment_is_used_when_no_request(monkeypatch):
    monkeypatch.setenv(runtime.THREAD_ENV, "5")
    assert runtime.resolve_threads() == 5


def test_legacy_instagram_environment_remains_supported(monkeypatch):
    monkeypatch.delenv(runtime.THREAD_ENV, raising=False)
    monkeypatch.setenv(runtime.LEGACY_THREAD_ENV, "6")
    assert runtime.resolve_threads() == 6


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


def test_best_resolves_to_an_engine_name_for_every_caller(monkeypatch):
    """`best` reached make_engine unresolved and matched no branch, so the
    pipeline raised "no OCR engine is available ()" and OCR never ran at all."""
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert runtime.resolve_engine_name("best", "image") == "surya"
    assert runtime.resolve_engine_name("best", "video") == "vision"
    # Anything already naming an engine is passed through untouched.
    assert runtime.resolve_engine_name("auto", "video") == "auto"
    assert runtime.resolve_engine_name("tesseract", "image") == "tesseract"


def test_best_falls_through_when_the_platform_pick_is_not_installed(monkeypatch):
    """It is a preference, not a demand: pyobjc is absent on Python 3.14."""
    from info_triage.extractors.media import engines

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert engines._candidates("best", "video") == ("vision", "rapidocr", "surya", "tesseract")
    # An explicit name still gets one shot, so its own error is what surfaces.
    assert engines._candidates("vision", "video") == ("vision",)

"""Strict YAML configuration for the Info Triage daemon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TRANSCRIPTION_BACKENDS = ("faster-whisper", "mlx")
TRANSCRIPTION_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
TRANSFORM_STEP_NAMES = ("url-resolution", "text-cleaning")
YT_DLP_CHANNELS = ("stable", "nightly", "master")


class ConfigError(ValueError):
    """Raised when daemon configuration is missing or invalid."""


@dataclass(frozen=True)
class VoiceTranscriptionConfig:
    name: str
    backend: str
    model: str
    model_cache_dir: Path
    threads: int


@dataclass(frozen=True)
class URLResolutionConfig:
    name: str
    timeout_seconds: float
    retries: int
    max_html_bytes: int
    max_pdf_bytes: int
    resolve_all: bool


@dataclass(frozen=True)
class TextCleaningConfig:
    name: str


StepConfig = VoiceTranscriptionConfig | URLResolutionConfig | TextCleaningConfig


@dataclass(frozen=True)
class YouTubeExtractorConfig:
    max_attempts: int
    retry_backoff_seconds: float
    max_parent_comments: int
    max_replies: int
    max_replies_per_thread: int
    update_channel: str
    update_check_interval_hours: float
    update_on_compatibility_error: bool


@dataclass(frozen=True)
class InstagramExtractorConfig:
    max_attempts: int
    retry_backoff_seconds: float


@dataclass(frozen=True)
class AppConfig:
    path: Path
    data_dir: Path
    web_port: int
    grouping_max_gap_seconds: float
    grouping_settle_seconds: float
    processing_steps: tuple[StepConfig, ...]
    youtube_extractor: YouTubeExtractorConfig
    instagram_extractor: InstagramExtractorConfig


def _mapping(value: Any, context: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{context} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError(f"{context} contains unknown field(s): {names}")
    return value


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{context}.{key} is required")
    return mapping[key]


def _number(value: Any, context: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{context} must be a number")
    result = float(value)
    if result <= minimum:
        raise ConfigError(f"{context} must be greater than {minimum:g}")
    return result


def _integer(value: Any, context: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{context} must be an integer")
    if value < minimum:
        raise ConfigError(f"{context} must be at least {minimum}")
    return value


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{context} must be true or false")
    return value


def _path(value: Any, context: str, base_dir: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context} must be a non-empty path string")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _parse_step(value: Any, index: int, base_dir: Path) -> StepConfig:
    context = f"processing.steps[{index}]"
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a mapping")
    name = value.get("name")
    if not isinstance(name, str):
        raise ConfigError(f"{context}.name must be a string")

    if name == "voice-transcription":
        step = _mapping(
            value,
            context,
            {"name", "backend", "model", "model_cache_dir", "threads"},
        )
        backend = _required(step, "backend", context)
        model = _required(step, "model", context)
        if backend not in TRANSCRIPTION_BACKENDS:
            raise ConfigError(
                f"{context}.backend must be one of: {', '.join(TRANSCRIPTION_BACKENDS)}"
            )
        if model not in TRANSCRIPTION_MODELS:
            raise ConfigError(f"{context}.model must be one of: {', '.join(TRANSCRIPTION_MODELS)}")
        return VoiceTranscriptionConfig(
            name,
            backend,
            model,
            _path(
                _required(step, "model_cache_dir", context),
                f"{context}.model_cache_dir",
                base_dir,
            ),
            _integer(
                _required(step, "threads", context),
                f"{context}.threads",
                minimum=1,
            ),
        )

    if name == "url-resolution":
        step = _mapping(
            value,
            context,
            {
                "name",
                "timeout_seconds",
                "retries",
                "max_html_bytes",
                "max_pdf_bytes",
                "resolve_all",
            },
        )
        return URLResolutionConfig(
            name,
            _number(
                _required(step, "timeout_seconds", context),
                f"{context}.timeout_seconds",
            ),
            _integer(
                _required(step, "retries", context),
                f"{context}.retries",
                minimum=0,
            ),
            _integer(
                _required(step, "max_html_bytes", context),
                f"{context}.max_html_bytes",
                minimum=1,
            ),
            _integer(
                _required(step, "max_pdf_bytes", context),
                f"{context}.max_pdf_bytes",
                minimum=1,
            ),
            _boolean(
                _required(step, "resolve_all", context),
                f"{context}.resolve_all",
            ),
        )

    if name == "text-cleaning":
        _mapping(value, context, {"name"})
        return TextCleaningConfig(name)

    raise ConfigError(f"{context}.name is unknown: {name}")


def _parse_extractors(value: Any) -> tuple[YouTubeExtractorConfig, InstagramExtractorConfig]:
    extractors = _mapping(value, "extractors", {"youtube", "instagram"})
    youtube = _mapping(
        _required(extractors, "youtube", "extractors"),
        "extractors.youtube",
        {
            "max_attempts",
            "retry_backoff_seconds",
            "max_parent_comments",
            "max_replies",
            "max_replies_per_thread",
            "update_channel",
            "update_check_interval_hours",
            "update_on_compatibility_error",
        },
    )
    instagram = _mapping(
        _required(extractors, "instagram", "extractors"),
        "extractors.instagram",
        {"max_attempts", "retry_backoff_seconds"},
    )
    channel = _required(youtube, "update_channel", "extractors.youtube")
    if channel not in YT_DLP_CHANNELS:
        raise ConfigError(
            "extractors.youtube.update_channel must be one of: " + ", ".join(YT_DLP_CHANNELS)
        )
    return (
        YouTubeExtractorConfig(
            max_attempts=_integer(
                _required(youtube, "max_attempts", "extractors.youtube"),
                "extractors.youtube.max_attempts",
                minimum=1,
            ),
            retry_backoff_seconds=_number(
                _required(youtube, "retry_backoff_seconds", "extractors.youtube"),
                "extractors.youtube.retry_backoff_seconds",
            ),
            max_parent_comments=_integer(
                _required(youtube, "max_parent_comments", "extractors.youtube"),
                "extractors.youtube.max_parent_comments",
                minimum=0,
            ),
            max_replies=_integer(
                _required(youtube, "max_replies", "extractors.youtube"),
                "extractors.youtube.max_replies",
                minimum=0,
            ),
            max_replies_per_thread=_integer(
                _required(youtube, "max_replies_per_thread", "extractors.youtube"),
                "extractors.youtube.max_replies_per_thread",
                minimum=0,
            ),
            update_channel=channel,
            update_check_interval_hours=_number(
                _required(youtube, "update_check_interval_hours", "extractors.youtube"),
                "extractors.youtube.update_check_interval_hours",
            ),
            update_on_compatibility_error=_boolean(
                _required(youtube, "update_on_compatibility_error", "extractors.youtube"),
                "extractors.youtube.update_on_compatibility_error",
            ),
        ),
        InstagramExtractorConfig(
            max_attempts=_integer(
                _required(instagram, "max_attempts", "extractors.instagram"),
                "extractors.instagram.max_attempts",
                minimum=1,
            ),
            retry_backoff_seconds=_number(
                _required(instagram, "retry_backoff_seconds", "extractors.instagram"),
                "extractors.instagram.retry_backoff_seconds",
            ),
        ),
    )


def load_config(path: Path) -> AppConfig:
    """Load and validate one daemon configuration file."""
    config_path = path.expanduser().resolve()
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file does not exist: {config_path}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {config_path}: {error}") from error

    root = _mapping(
        value,
        "configuration",
        {"storage", "web", "telegram", "processing", "extractors"},
    )
    base_dir = config_path.parent

    storage = _mapping(
        _required(root, "storage", "configuration"),
        "storage",
        {"data_dir"},
    )
    web = _mapping(_required(root, "web", "configuration"), "web", {"port"})
    telegram = _mapping(
        _required(root, "telegram", "configuration"),
        "telegram",
        {"grouping"},
    )
    grouping = _mapping(
        _required(telegram, "grouping", "telegram"),
        "telegram.grouping",
        {"max_gap_seconds", "settle_seconds"},
    )
    processing = _mapping(
        _required(root, "processing", "configuration"),
        "processing",
        {"steps"},
    )

    port = _integer(_required(web, "port", "web"), "web.port", minimum=1)
    if port > 65535:
        raise ConfigError("web.port must be at most 65535")
    max_gap = _number(
        _required(grouping, "max_gap_seconds", "telegram.grouping"),
        "telegram.grouping.max_gap_seconds",
    )
    settle = _number(
        _required(grouping, "settle_seconds", "telegram.grouping"),
        "telegram.grouping.settle_seconds",
    )
    if settle <= max_gap:
        raise ConfigError("telegram.grouping.settle_seconds must be greater than max_gap_seconds")

    step_values = _required(processing, "steps", "processing")
    if not isinstance(step_values, list):
        raise ConfigError("processing.steps must be a list")
    steps = tuple(_parse_step(value, index, base_dir) for index, value in enumerate(step_values))
    names = [step.name for step in steps]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"processing.steps contains duplicate step(s): {', '.join(duplicates)}")
    if "voice-transcription" in names:
        voice_index = names.index("voice-transcription")
        if any(names.index(name) < voice_index for name in TRANSFORM_STEP_NAMES if name in names):
            raise ConfigError(
                "voice-transcription must appear before URL resolution and text cleaning"
            )

    youtube_extractor, instagram_extractor = _parse_extractors(
        _required(root, "extractors", "configuration")
    )

    return AppConfig(
        path=config_path,
        data_dir=_path(
            _required(storage, "data_dir", "storage"),
            "storage.data_dir",
            base_dir,
        ),
        web_port=port,
        grouping_max_gap_seconds=max_gap,
        grouping_settle_seconds=settle,
        processing_steps=steps,
        youtube_extractor=youtube_extractor,
        instagram_extractor=instagram_extractor,
    )

"""Strict YAML configuration for the Info Triage daemon."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: Every route the daemon knows about. A route is a capture-time pipeline switch:
#: one Telegram bot, one ordered step list, one `data/inbox/<route>/` tree.
#: `info_triage/sync.py` keeps a matching copy so the laptop side stays
#: independent of this module; the two must not drift.
ROUTE_NAMES = ("info", "job", "clip", "lang")

TRANSCRIPTION_BACKENDS = ("faster-whisper", "mlx")
TRANSCRIPTION_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
TRANSFORM_STEP_NAMES = (
    "url-resolution",
    "text-cleaning",
    "link-discovery",
    "content-extraction",
    "index-render",
)
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
    resolve_budget: int


@dataclass(frozen=True)
class TextCleaningConfig:
    name: str


@dataclass(frozen=True)
class LinkDiscoveryConfig:
    name: str


@dataclass(frozen=True)
class ContentExtractionConfig:
    name: str
    extract_budget: int
    linklist_extract_budget: int
    wall_clock_seconds: float
    keep_raw: bool


@dataclass(frozen=True)
class IndexRenderConfig:
    name: str
    lead_words: int
    media_lead_words: int


StepConfig = (
    VoiceTranscriptionConfig
    | URLResolutionConfig
    | TextCleaningConfig
    | LinkDiscoveryConfig
    | ContentExtractionConfig
    | IndexRenderConfig
)


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
    # Instagram rate-limits anonymous access and hides comments entirely. These
    # stay unset until a session is mounted; treat either as a credential.
    session_file: Path | None = None
    instagram_user: str | None = None
    cookies_file: Path | None = None


@dataclass(frozen=True)
class MediumExtractorConfig:
    # Without a member session Medium returns title plus a few paragraphs, which
    # is enough to route on and not enough to judge. See PREVIEW_REASON.
    cookie_file: Path | None = None


@dataclass(frozen=True)
class RouteConfig:
    name: str
    #: Name of the environment variable holding this route's bot token. The token
    #: itself never enters config.yaml; naming the variable here keeps the binding
    #: greppable and turns a typo into a load-time error.
    token_env: str
    steps: tuple[StepConfig, ...]


@dataclass(frozen=True)
class AppConfig:
    path: Path
    data_dir: Path
    web_port: int
    capture_token_env: str
    grouping_max_gap_seconds: float
    grouping_settle_seconds: float
    routes: tuple[RouteConfig, ...]
    linklist_threshold: int
    youtube_extractor: YouTubeExtractorConfig
    instagram_extractor: InstagramExtractorConfig
    medium_extractor: MediumExtractorConfig

    def route(self, name: str) -> RouteConfig:
        for route in self.routes:
            if route.name == name:
                return route
        raise KeyError(name)


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


def _optional_path(value: Any, context: str, base_dir: Path) -> Path | None:
    return None if value is None else _path(value, context, base_dir)


def _optional_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context} must be a non-empty string")
    return value.strip()


def _parse_step(value: Any, context: str, base_dir: Path) -> StepConfig:
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
                "resolve_budget",
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
            _integer(
                _required(step, "resolve_budget", context),
                f"{context}.resolve_budget",
                minimum=1,
            ),
        )

    if name == "text-cleaning":
        _mapping(value, context, {"name"})
        return TextCleaningConfig(name)

    if name == "link-discovery":
        _mapping(value, context, {"name"})
        return LinkDiscoveryConfig(name)

    if name == "content-extraction":
        step = _mapping(
            value,
            context,
            {
                "name",
                "extract_budget",
                "linklist_extract_budget",
                "wall_clock_seconds",
                "keep_raw",
            },
        )
        return ContentExtractionConfig(
            name,
            _integer(
                _required(step, "extract_budget", context),
                f"{context}.extract_budget",
                minimum=0,
            ),
            _integer(
                _required(step, "linklist_extract_budget", context),
                f"{context}.linklist_extract_budget",
                minimum=0,
            ),
            _number(
                _required(step, "wall_clock_seconds", context),
                f"{context}.wall_clock_seconds",
            ),
            _boolean(_required(step, "keep_raw", context), f"{context}.keep_raw"),
        )

    if name == "index-render":
        step = _mapping(value, context, {"name", "lead_words", "media_lead_words"})
        return IndexRenderConfig(
            name,
            _integer(
                _required(step, "lead_words", context),
                f"{context}.lead_words",
                minimum=1,
            ),
            _integer(
                _required(step, "media_lead_words", context),
                f"{context}.media_lead_words",
                minimum=1,
            ),
        )

    raise ConfigError(f"{context}.name is unknown: {name}")


def _parse_steps(values: Any, context: str, base_dir: Path) -> tuple[StepConfig, ...]:
    if not isinstance(values, list):
        raise ConfigError(f"{context} must be a list")
    steps = tuple(
        _parse_step(value, f"{context}[{index}]", base_dir) for index, value in enumerate(values)
    )
    _validate_step_order(steps, context)
    return steps


def _validate_step_order(steps: tuple[StepConfig, ...], context: str) -> None:
    """Apply the ordering rules within one route's step list.

    Per route, not globally: two routes may each render an index, but one route
    may not render two.
    """
    names = [step.name for step in steps]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"{context} contains duplicate step(s): {', '.join(duplicates)}")
    if "voice-transcription" in names:
        voice_index = names.index("voice-transcription")
        if any(names.index(name) < voice_index for name in TRANSFORM_STEP_NAMES if name in names):
            raise ConfigError(
                f"{context}: voice-transcription must appear before every transform step"
            )
    if "link-discovery" in names and "url-resolution" in names:
        if names.index("url-resolution") < names.index("link-discovery"):
            raise ConfigError(f"{context}: link-discovery must appear before url-resolution")
    if "content-extraction" in names and "url-resolution" in names:
        if names.index("content-extraction") < names.index("url-resolution"):
            raise ConfigError(f"{context}: url-resolution must appear before content-extraction")
    if "index-render" in names and names[-1] != "index-render":
        raise ConfigError(f"{context}: index-render must be the last processing step")


def _parse_routes(value: Any, base_dir: Path) -> tuple[RouteConfig, ...]:
    if not isinstance(value, list):
        raise ConfigError("routes must be a list")
    routes = []
    for index, entry in enumerate(value):
        context = f"routes[{index}]"
        route = _mapping(entry, context, {"name", "token_env", "steps"})
        name = _required(route, "name", context)
        if name not in ROUTE_NAMES:
            raise ConfigError(f"{context}.name must be one of: {', '.join(ROUTE_NAMES)}")
        token_env = _required(route, "token_env", context)
        if not isinstance(token_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", token_env):
            raise ConfigError(
                f"{context}.token_env must be an upper-case environment variable name"
            )
        steps = _parse_steps(_required(route, "steps", context), f"{context}.steps", base_dir)
        routes.append(RouteConfig(name, token_env, steps))

    names = [route.name for route in routes]
    if sorted(names) != sorted(ROUTE_NAMES):
        raise ConfigError(f"routes must declare exactly: {', '.join(ROUTE_NAMES)}")
    tokens = [route.token_env for route in routes]
    # Two routes on one token means two pollers on one bot. Telegram answers the
    # second getUpdates with a 409 and both routes then miss messages.
    if len(set(tokens)) != len(tokens):
        raise ConfigError("routes must not share a token_env")
    return tuple(routes)


def _parse_extractors(
    value: Any, base_dir: Path
) -> tuple[YouTubeExtractorConfig, InstagramExtractorConfig, MediumExtractorConfig]:
    extractors = _mapping(value, "extractors", {"youtube", "instagram", "medium"})
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
        {
            "max_attempts",
            "retry_backoff_seconds",
            "session_file",
            "instagram_user",
            "cookies_file",
        },
    )
    medium = _mapping(extractors.get("medium") or {}, "extractors.medium", {"cookie_file"})
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
            session_file=_optional_path(
                instagram.get("session_file"), "extractors.instagram.session_file", base_dir
            ),
            instagram_user=_optional_string(
                instagram.get("instagram_user"), "extractors.instagram.instagram_user"
            ),
            cookies_file=_optional_path(
                instagram.get("cookies_file"), "extractors.instagram.cookies_file", base_dir
            ),
        ),
        MediumExtractorConfig(
            cookie_file=_optional_path(
                medium.get("cookie_file"), "extractors.medium.cookie_file", base_dir
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
        {"storage", "web", "telegram", "processing", "routes", "extractors"},
    )
    base_dir = config_path.parent

    storage = _mapping(
        _required(root, "storage", "configuration"),
        "storage",
        {"data_dir"},
    )
    web = _mapping(_required(root, "web", "configuration"), "web", {"port", "capture_token_env"})
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
        {"linklist_threshold"},
    )

    port = _integer(_required(web, "port", "web"), "web.port", minimum=1)
    if port > 65535:
        raise ConfigError("web.port must be at most 65535")
    capture_token_env = _required(web, "capture_token_env", "web")
    if not isinstance(capture_token_env, str) or not re.fullmatch(
        r"[A-Z][A-Z0-9_]*", capture_token_env
    ):
        raise ConfigError("web.capture_token_env must be an upper-case environment variable name")
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

    routes = _parse_routes(_required(root, "routes", "configuration"), base_dir)

    linklist_threshold = _integer(
        _required(processing, "linklist_threshold", "processing"),
        "processing.linklist_threshold",
        minimum=1,
    )
    youtube_extractor, instagram_extractor, medium_extractor = _parse_extractors(
        _required(root, "extractors", "configuration"), base_dir
    )

    return AppConfig(
        path=config_path,
        data_dir=_path(
            _required(storage, "data_dir", "storage"),
            "storage.data_dir",
            base_dir,
        ),
        web_port=port,
        capture_token_env=capture_token_env,
        grouping_max_gap_seconds=max_gap,
        grouping_settle_seconds=settle,
        routes=routes,
        linklist_threshold=linklist_threshold,
        youtube_extractor=youtube_extractor,
        instagram_extractor=instagram_extractor,
        medium_extractor=medium_extractor,
    )

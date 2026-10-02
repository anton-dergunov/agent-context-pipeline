from pathlib import Path

import pytest

from info_triage.config import (
    ConfigError,
    ContentExtractionConfig,
    IndexRenderConfig,
    InstagramExtractorConfig,
    LinkDiscoveryConfig,
    TextCleaningConfig,
    URLResolutionConfig,
    VoiceTranscriptionConfig,
    YouTubeExtractorConfig,
    load_config,
)

REPOSITORY = Path(__file__).resolve().parents[1]

MINIMAL_STEPS = (
    "      - name: index-render\n        lead_words: 120\n        media_lead_words: 800\n"
)


def config_text(
    *,
    steps: str,
    data_dir: str = "state",
    linklist_threshold: int = 8,
    other_steps: str = MINIMAL_STEPS,
) -> str:
    """Configuration whose `info` route carries `steps`, indented two more spaces.

    The other three routes are always present and always minimal: `routes` must
    declare all four, and these tests are about one route's step list.
    """
    info_steps = "".join(f"  {line}\n" if line else "\n" for line in steps.splitlines())
    return f"""
storage:
  data_dir: {data_dir}
web:
  port: 8123
  capture_token_env: INFO_TRIAGE_CAPTURE_TOKEN
telegram:
  grouping:
    max_gap_seconds: 2.5
    settle_seconds: 3.5
extractors:
  youtube:
    max_attempts: 3
    retry_backoff_seconds: 5
    max_parent_comments: 5
    max_replies: 10
    max_replies_per_thread: 2
    update_channel: nightly
    update_check_interval_hours: 24
    update_on_compatibility_error: true
  instagram:
    max_attempts: 3
    retry_backoff_seconds: 5
processing:
  linklist_threshold: {linklist_threshold}
routes:
  - name: info
    token_env: TELEGRAM_BOT_TOKEN_INFO
    steps:
{info_steps}  - name: job
    token_env: TELEGRAM_BOT_TOKEN_JOB
    steps:
{other_steps}  - name: clip
    token_env: TELEGRAM_BOT_TOKEN_CLIP
    steps:
{other_steps}  - name: lang
    token_env: TELEGRAM_BOT_TOKEN_LANG
    steps:
{other_steps}"""


def test_shipped_config_has_expected_order_and_explicit_nas_model():
    config = load_config(REPOSITORY / "config.example.yaml")

    info = config.route("info")
    assert [step.name for step in info.steps] == [
        "voice-transcription",
        "text-cleaning",
        "link-discovery",
        "url-resolution",
        "content-extraction",
        "index-render",
    ]
    resolution = info.steps[3]
    assert isinstance(resolution, URLResolutionConfig)
    assert resolution.resolve_budget == 40
    extraction = info.steps[4]
    assert isinstance(extraction, ContentExtractionConfig)
    assert extraction.extract_budget == 5
    assert extraction.linklist_extract_budget == 2
    assert extraction.wall_clock_seconds == 600
    assert extraction.keep_raw is True
    render = info.steps[5]
    assert isinstance(render, IndexRenderConfig)
    assert render.lead_words == 120
    assert config.linklist_threshold == 8
    voice = info.steps[0]
    assert isinstance(voice, VoiceTranscriptionConfig)
    assert voice.backend == "faster-whisper"
    assert voice.model == "small"
    assert voice.threads == 1
    assert voice.model_cache_dir == REPOSITORY / ".whisper_models"
    assert isinstance(config.youtube_extractor, YouTubeExtractorConfig)
    assert config.youtube_extractor.max_parent_comments == 5
    assert config.youtube_extractor.max_replies == 10
    assert config.youtube_extractor.update_channel == "nightly"
    assert isinstance(config.instagram_extractor, InstagramExtractorConfig)
    assert config.instagram_extractor.max_attempts == 3


def test_shipped_pass_through_routes_never_retrieve_or_transform():
    """job, clip and lang record what arrived; only info enriches it."""
    config = load_config(REPOSITORY / "config.example.yaml")

    assert [route.name for route in config.routes] == ["info", "job", "clip", "lang"]
    assert [step.name for step in config.route("job").steps] == ["link-discovery", "index-render"]
    assert [step.name for step in config.route("clip").steps] == ["link-discovery", "index-render"]
    assert [step.name for step in config.route("lang").steps] == ["index-render"]
    assert len({route.token_env for route in config.routes}) == 4
    assert config.capture_token_env == "INFO_TRIAGE_CAPTURE_TOKEN"


def test_custom_config_resolves_paths_relative_to_itself_and_ignores_old_env(tmp_path, monkeypatch):
    path = tmp_path / "daemon.yaml"
    path.write_text(
        config_text(
            data_dir="relative-data",
            steps="""    - name: link-discovery
    - name: url-resolution
      timeout_seconds: 1.5
      retries: 0
      max_html_bytes: 1024
      max_pdf_bytes: 2048
      resolve_all: false
      resolve_budget: 7
    - name: text-cleaning
""",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", "/ignored")
    monkeypatch.setenv("PORT", "9999")

    config = load_config(path)

    steps = config.route("info").steps
    assert config.data_dir == tmp_path / "relative-data"
    assert config.web_port == 8123
    assert isinstance(steps[0], LinkDiscoveryConfig)
    assert isinstance(steps[1], URLResolutionConfig)
    assert steps[1].max_pdf_bytes == 2048
    assert steps[1].resolve_budget == 7
    assert isinstance(steps[2], TextCleaningConfig)


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            """    - name: text-cleaning
    - name: text-cleaning
""",
            "duplicate step",
        ),
        (
            """    - name: text-cleaning
    - name: voice-transcription
      backend: faster-whisper
      model: small
      model_cache_dir: models
      threads: 1
""",
            "must appear before",
        ),
        ("    - name: invented\n", "is unknown"),
        ("    - name: text-cleaning\n      surprise: true\n", "unknown field"),
        ("    - name: link-discovery\n      budget: 3\n", "unknown field"),
        (
            """    - name: url-resolution
      timeout_seconds: 1.5
      retries: 0
      max_html_bytes: 1024
      max_pdf_bytes: 2048
      resolve_all: false
      resolve_budget: 40
    - name: link-discovery
""",
            "link-discovery must appear before url-resolution",
        ),
        (
            """    - name: url-resolution
      timeout_seconds: 1.5
      retries: 0
      max_html_bytes: 1024
      max_pdf_bytes: 2048
      resolve_all: false
      resolve_budget: 0
""",
            "resolve_budget must be at least 1",
        ),
        (
            """    - name: index-render
      lead_words: 120
      media_lead_words: 800
    - name: text-cleaning
""",
            "index-render must be the last processing step",
        ),
        (
            "    - name: index-render\n      lead_words: 0\n      media_lead_words: 800\n",
            "lead_words must be at least 1",
        ),
        ("    - name: index-render\n", "lead_words is required"),
        (
            "    - name: index-render\n      lead_words: 120\n",
            "media_lead_words is required",
        ),
        (
            "    - name: content-extraction\n      extract_budget: 5\n"
            "      linklist_extract_budget: 2\n      wall_clock_seconds: 600\n"
            "      keep_raw: true\n    - name: url-resolution\n"
            "      timeout_seconds: 10.0\n      retries: 1\n"
            "      max_html_bytes: 1024\n      max_pdf_bytes: 1024\n"
            "      resolve_all: false\n      resolve_budget: 40\n",
            "url-resolution must appear before content-extraction",
        ),
    ],
)
def test_invalid_processing_configuration_is_rejected(tmp_path, steps, message):
    path = tmp_path / "config.yaml"
    path.write_text(config_text(steps=steps), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_step_errors_name_the_route_they_came_from(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(config_text(steps="    - name: invented\n"), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"routes\[0\]\.steps\[0\]\.name is unknown"):
        load_config(path)


def test_one_step_may_appear_once_per_route_but_on_every_route(tmp_path):
    """The duplicate rule is per route: four routes each rendering an index is fine."""
    path = tmp_path / "config.yaml"
    path.write_text(config_text(steps=MINIMAL_STEPS.replace("      ", "    ")), encoding="utf-8")

    config = load_config(path)

    assert [[step.name for step in route.steps] for route in config.routes] == [
        ["index-render"]
    ] * 4


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (
            ("token_env: TELEGRAM_BOT_TOKEN_JOB", "token_env: TELEGRAM_BOT_TOKEN_INFO"),
            "must not share a token_env",
        ),
        (("name: lang", "name: info"), "routes must declare exactly"),
        (("name: clip", "name: invented"), r"routes\[2\]\.name must be one of"),
        (
            ("token_env: TELEGRAM_BOT_TOKEN_CLIP", "token_env: not-an-env-var"),
            "must be an upper-case environment variable name",
        ),
    ],
)
def test_invalid_route_configuration_is_rejected(tmp_path, replacement, message):
    old, new = replacement
    path = tmp_path / "config.yaml"
    path.write_text(
        config_text(steps=MINIMAL_STEPS.replace("      ", "    ")).replace(old, new, 1),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_settle_delay_must_exceed_grouping_gap(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        config_text(steps="    - name: text-cleaning\n").replace(
            "settle_seconds: 3.5", "settle_seconds: 2.5"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must be greater"):
        load_config(path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("update_channel: nightly", "update_channel: invented", "update_channel"),
        ("max_attempts: 3", "max_attempts: 0", "max_attempts"),
        (
            "update_on_compatibility_error: true",
            "update_on_compatibility_error: sometimes",
            "must be true or false",
        ),
    ],
)
def test_invalid_extractor_configuration_is_rejected(tmp_path, old, new, message):
    path = tmp_path / "config.yaml"
    path.write_text(
        config_text(steps="    - name: text-cleaning\n").replace(old, new, 1),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(path)

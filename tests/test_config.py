from pathlib import Path

import pytest

from info_triage.config import (
    ConfigError,
    InstagramExtractorConfig,
    TextCleaningConfig,
    URLResolutionConfig,
    VoiceTranscriptionConfig,
    YouTubeExtractorConfig,
    load_config,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def config_text(*, steps: str, data_dir: str = "state") -> str:
    return f"""
storage:
  data_dir: {data_dir}
web:
  port: 8123
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
  steps:
{steps}
"""


def test_shipped_config_has_expected_order_and_explicit_nas_model():
    config = load_config(REPOSITORY / "config.yaml")

    assert [step.name for step in config.processing_steps] == [
        "voice-transcription",
        "text-cleaning",
        "url-resolution",
    ]
    voice = config.processing_steps[0]
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


def test_custom_config_resolves_paths_relative_to_itself_and_ignores_old_env(tmp_path, monkeypatch):
    path = tmp_path / "daemon.yaml"
    path.write_text(
        config_text(
            data_dir="relative-data",
            steps="""    - name: url-resolution
      timeout_seconds: 1.5
      retries: 0
      max_html_bytes: 1024
      max_pdf_bytes: 2048
      resolve_all: false
    - name: text-cleaning
""",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", "/ignored")
    monkeypatch.setenv("PORT", "9999")

    config = load_config(path)

    assert config.data_dir == tmp_path / "relative-data"
    assert config.web_port == 8123
    assert isinstance(config.processing_steps[0], URLResolutionConfig)
    assert config.processing_steps[0].max_pdf_bytes == 2048
    assert isinstance(config.processing_steps[1], TextCleaningConfig)


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
    ],
)
def test_invalid_processing_configuration_is_rejected(tmp_path, steps, message):
    path = tmp_path / "config.yaml"
    path.write_text(config_text(steps=steps), encoding="utf-8")

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

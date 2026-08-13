from pathlib import Path

import pytest

from info_triage.config import (
    ConfigError,
    TextCleaningConfig,
    URLResolutionConfig,
    VoiceTranscriptionConfig,
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
processing:
  steps:
{steps}
"""


def test_shipped_config_has_expected_order_and_explicit_nas_model():
    config = load_config(REPOSITORY / "config.yaml")

    assert [step.name for step in config.processing_steps] == [
        "voice-transcription",
        "url-resolution",
        "text-cleaning",
    ]
    voice = config.processing_steps[0]
    assert isinstance(voice, VoiceTranscriptionConfig)
    assert voice.backend == "faster-whisper"
    assert voice.model == "small"
    assert voice.threads == 1
    assert voice.model_cache_dir == REPOSITORY / ".whisper_models"


def test_custom_config_resolves_paths_relative_to_itself_and_ignores_old_env(tmp_path, monkeypatch):
    path = tmp_path / "daemon.yaml"
    path.write_text(
        config_text(
            data_dir="relative-data",
            steps="""    - name: url-resolution
      timeout_seconds: 1.5
      retries: 0
      max_html_bytes: 1024
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

from pathlib import Path

from info_triage.extractors.youtube.cli import build_parser, main


def test_youtube_cli_exposes_standalone_controls():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--kind",
            "short",
            "--max-comments",
            "7",
            "--max-replies",
            "12",
            "--skip-comments",
            "--no-update-on-compatibility-error",
            "--video-sample-fps",
            "3",
            "https://youtu.be/59CAwbGTHLE",
        ]
    )
    assert parser.prog == "youtube-extract"
    assert args.kind == "short"
    assert args.max_comments == 7
    assert args.max_replies == 12
    assert args.skip_comments is True
    assert args.update_on_compatibility_error is False


def test_cli_values_override_yaml_defaults(monkeypatch, tmp_path):
    captured = {}

    class FakeRunner:
        def __init__(self, settings):
            captured["settings"] = settings

    class FakeExtractor:
        def __init__(self, runner, options, **_kwargs):
            captured["options"] = options

        def extract(self, reference):
            output = captured["options"].output_dir / reference.video_id
            output.mkdir(parents=True)
            return output

        def close(self):
            return None

    monkeypatch.setattr("info_triage.extractors.youtube.cli.ManagedYtDlp", FakeRunner)
    monkeypatch.setattr("info_triage.extractors.youtube.cli.YouTubeExtractor", FakeExtractor)
    repository = Path(__file__).resolve().parents[3]
    result = main(
        [
            "--config",
            str(repository / "config.yaml"),
            "--output-dir",
            str(tmp_path),
            "--max-comments",
            "9",
            "--max-attempts",
            "1",
            "--retry-backoff-seconds",
            "0.25",
            "--skip-ocr",
            "--skip-transcript",
            "https://youtu.be/59CAwbGTHLE",
        ]
    )
    assert result == 0
    assert captured["options"].max_parent_comments == 9
    assert captured["settings"].max_attempts == 1
    assert captured["settings"].retry_backoff_seconds == 0.25

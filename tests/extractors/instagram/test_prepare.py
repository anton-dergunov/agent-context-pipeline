"""Tests for Instagram downstream input preparation."""

import json

from info_triage.extractors.instagram.prepare import prepare_llm_input


def test_prepare_includes_spoken_audio_in_both_llm_formats(tmp_path):
    post_dir = tmp_path / "post"
    (post_dir / "transcripts").mkdir(parents=True)
    (post_dir / "metadata.json").write_text(
        json.dumps({"shortcode": "abc", "caption": "Caption"}), encoding="utf-8"
    )
    (post_dir / "transcript.txt").write_text("Hola мир\n", encoding="utf-8")
    (post_dir / "transcripts/01_video.json").write_text(
        json.dumps(
            {
                "source_file": "01_video.mp4",
                "status": "complete",
                "backend": "mlx",
                "model": "medium",
                "language": "es",
                "language_probability": None,
                "text": "Hola мир",
            }
        ),
        encoding="utf-8",
    )

    prepare_llm_input(post_dir)

    payload = json.loads((post_dir / "llm_input.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["spoken_audio"][0]["text"] == "Hola мир"
    readable = (post_dir / "llm_input.txt").read_text(encoding="utf-8")
    assert "SPOKEN AUDIO\nHola мир" in readable

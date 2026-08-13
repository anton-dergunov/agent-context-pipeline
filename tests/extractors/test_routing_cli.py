from pathlib import Path

from info_triage.extractors import routing_cli
from info_triage.extractors.router import GenericReference, RouteResult


def test_cli_passes_trailing_options_to_selected_extractor(monkeypatch, tmp_path):
    route = RouteResult(
        "document",
        "id",
        "https://example.com",
        "https://example.com",
        GenericReference("https://example.com", "https://example.com/", "id"),
    )
    monkeypatch.setattr(routing_cli, "route_url", lambda _url: route)
    captured = {}

    def fake_execute(actual_route, output_dir, forwarded):
        captured.update(route=actual_route, output_dir=output_dir, forwarded=forwarded)
        return 7

    monkeypatch.setattr(routing_cli, "_execute", fake_execute)
    code = routing_cli.main(
        [
            "--output-dir",
            str(tmp_path),
            "https://example.com",
            "--",
            "--no-keep-raw",
            "--timeout",
            "5",
        ]
    )
    assert code == 7
    assert captured["output_dir"] == Path(tmp_path)
    assert captured["forwarded"] == ["--no-keep-raw", "--timeout", "5"]

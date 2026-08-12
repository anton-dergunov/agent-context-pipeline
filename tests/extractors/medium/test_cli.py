from info_triage.extractors.medium.cli import main

URL = "https://medium.com/example/story-abcdef123456"


def test_cli_rejects_unknown_method(capsys):
    assert main(["--methods", "rss,unknown", URL]) == 2
    assert "unknown Medium extraction method" in capsys.readouterr().err


def test_cli_rejects_duplicate_methods(capsys):
    assert main(["--methods", "rss,rss", URL]) == 2
    assert "must not be repeated" in capsys.readouterr().err

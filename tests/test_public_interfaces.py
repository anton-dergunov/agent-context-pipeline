"""Smoke tests for the integrated reusable APIs and console commands."""

from importlib.metadata import entry_points

from info_triage.extractors.instagram.cli import build_parser as instagram_parser
from info_triage.extractors.linkedin.cli import build_parser as linkedin_parser
from info_triage.extractors.youtube.cli import build_parser as youtube_parser
from info_triage.utilities.text_cleaning import clean_text
from info_triage.utilities.url_resolution import LinkResolution, URLResolver, enrich_links


def test_reusable_interfaces_import_from_info_triage():
    assert clean_text("Hello  world") == "Hello world"
    assert URLResolver(timeout=1, retries=0, max_html_bytes=100).timeout == 1
    assert LinkResolution("https://example.com", "Example").succeeded
    assert callable(enrich_links)
    assert instagram_parser().prog == "instagram-extract"
    assert linkedin_parser().prog == "linkedin-extract"
    assert youtube_parser().prog == "youtube-extract"


def test_console_entry_points_are_installed():
    commands = {
        point.name
        for point in entry_points(group="console_scripts")
        if point.value.startswith("info_triage.")
    }
    assert {
        "info-triage-clean-text",
        "info-triage-resolve-urls",
        "instagram-extract",
        "linkedin-extract",
        "youtube-extract",
    } <= commands

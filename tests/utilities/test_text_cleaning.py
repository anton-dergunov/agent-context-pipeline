"""Regression tests for the imported text-cleaning decisions."""

from pathlib import Path

import pytest

from info_triage.utilities.text_cleaning import clean_file, clean_line, clean_text


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("𝗣𝗼𝘁𝗲𝗻𝘁𝗶𝗮𝗹 𝗽𝗮𝗿𝗮𝗱𝗶𝗴𝗺 𝘀𝗵𝗶𝗳𝘁", "Potential paradigm shift"),
        ("2. 𝐀𝐠𝐞𝐧𝐭𝐬", "2. Agents"),
        ("It’s 𝗖𝗦𝟯𝟯𝟲: 𝗟𝗮𝗻𝗴𝘂𝗮𝗴𝗲 𝗠𝗼𝗱𝗲𝗹𝗶𝗻𝗴", "It's CS336: Language Modeling"),
        ("1️⃣ First", "1 First"),
        ("5️⃣ Chain-of-Thought", "5 Chain-of-Thought"),
        ("🔟 Tenth item", "10 Tenth item"),
        ("Free textbook! 📖✨", "Free textbook!"),
        ("Enjoy! 🌹", "Enjoy!"),
        ("3 ⭐ stars", "3 ⭐ stars"),
        ("Warning ⚠️ alert", "Warning ⚠️ alert"),
        ("Task ✅ completed", "Task ✅ completed"),
        ("Jailbreak 💪 ref", "Jailbreak ref"),
        ("#ArtificialIntelligence #DeepLearning", ""),
        ("This is #FeaturedFriday post", "This is FeaturedFriday post"),
        ("It is not hashtag#GenAI!", "It is not GenAI!"),
        ("Ranked #1 in class", "Ranked #1 in class"),
        (
            "Go to https://github.com/org/repo#bonus-material",
            "Go to https://github.com/org/repo#bonus-material",
        ),
        ("○ First bullet", "- First bullet"),
        ("🔹 Subpoint", "- Subpoint"),
        ("  🔹 Subpoint", "  * Subpoint"),
        ("📚 AI Engineering 👉 https://amzn.to", "AI Engineering -> https://amzn.to"),
        ("Let’s go — yes", "Let's go - yes"),
        ("#+TITLE: My Inbox", "#+TITLE: My Inbox"),
        ("#+begin_src python", "#+begin_src python"),
        ("Hello  world ! ", "Hello world!"),
        (
            "Click https://linkedin.com/posts/activity-123?utm_source=share&utm_medium=desktop&id=789",
            "Click https://linkedin.com/posts/activity-123?id=789",
        ),
        ("The cаt is eating data.", "The cat is eating data."),
        ("Лори Сантос", "Лори Сантос"),
        ("—-", "---"),
        ("—--", "---"),
        ("========", "---"),
    ],
)
def test_imported_line_cleaning_cases(source, expected):
    assert clean_line(source).rstrip("\n") == expected


def test_clean_text_collapses_blank_lines_and_removes_trailing_blanks():
    source = "First\n\n#OnlyAHashtag\n\n\nSecond\n\n"
    assert clean_text(source) == "First\n\nSecond\n"


def test_clean_file_matches_retained_fixture(tmp_path):
    source = Path("tests/fixtures/text_cleaning/input.txt")
    expected = Path("tests/fixtures/text_cleaning/output.txt").read_text(encoding="utf-8")
    output = tmp_path / "cleaned.txt"

    class Resolver:
        def resolve(self, url):
            assert url == "https://lnkd.in/d8kGVA29"
            return "https://www.deeplearning.ai/courses/generative-ai-for-everyone"

    clean_file(source, output, resolve_links=True, resolver=Resolver())

    assert output.read_text(encoding="utf-8") == expected


def test_clean_text_uses_injected_url_resolver_before_tracking_cleanup():
    class Resolver:
        def resolve(self, url):
            assert url == "https://bit.ly/example"
            return "https://example.com/article?utm_source=share&id=7"

    assert (
        clean_text(
            "Read https://bit.ly/example",
            resolve_links=True,
            resolver=Resolver(),
        )
        == "Read https://example.com/article?id=7"
    )

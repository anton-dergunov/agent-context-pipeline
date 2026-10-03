"""What styled Unicode, emoji and hashtags cost in tokens, before and after cleaning.

Prints aggregate counts only, so the output can be committed while the source stays private.
tiktoken's o200k_base stands in for a model's tokenizer; it is not the one any Claude model uses.
Run from the repository root:

    uv run --with tiktoken python experiments/text-cleaning/tokens.py <file>
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

import emoji
import tiktoken

from info_triage.utilities.text_cleaning import clean_line

STYLED = re.compile(r"[\U0001D400-\U0001D7FF]")  # Mathematical Alphanumeric Symbols
URL = re.compile(r"https?://\S+")
EXAMPLE = "𝗣𝗼𝘁𝗲𝗻𝘁𝗶𝗮𝗹 𝗽𝗮𝗿𝗮𝗱𝗶𝗴𝗺 𝘀𝗵𝗶𝗳𝘁"

encoding = tiktoken.get_encoding("o200k_base")


def tokens(text: str) -> int:
    return len(encoding.encode(text))


def main(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    styled = [line for line in lines if STYLED.search(line)]
    urls = [url for line in lines for url in URL.findall(line)]
    raw = sum(tokens(line) for line in lines)
    cleaned = sum(tokens(clean_line(line)) for line in lines)
    return {
        "lines": len(lines),
        "urls": len(urls),
        "lnkd_in_urls": sum("://lnkd.in/" in url for url in urls),
        "lines_with_emoji": sum(bool(emoji.emoji_count(line)) for line in lines),
        "lines_with_styled_letters": len(styled),
        "styled_lines_tokens": {
            "raw": sum(tokens(line) for line in styled),
            "nfkc_normalized": sum(tokens(unicodedata.normalize("NFKC", line)) for line in styled),
        },
        "whole_file_tokens": {"raw": raw, "cleaned": cleaned},
        "example": {
            "text": EXAMPLE,
            "raw": tokens(EXAMPLE),
            "nfkc_normalized": tokens(unicodedata.normalize("NFKC", EXAMPLE)),
        },
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1])), ensure_ascii=False, indent=2))

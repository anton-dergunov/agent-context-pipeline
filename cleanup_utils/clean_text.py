#!/usr/bin/env python3
"""
clean_text.py - Cautious text cleanup for social media news copy-pasted into Org-mode files.

Cleans up:
1) Standalone/inline hashtags.
2) Non-essential emojis (while preserving ⭐, 🌟, ✅, ✔, ⚠️, ⚠).
3) Fancy Unicode formatting (bold, italic, mathematical symbols) via NFKC normalization.
4) Keycap digits (e.g. 5️⃣ -> 5).
5) Fancy bullets (○, 🔹, etc.) to standard bullets (*) or arrows (->).
6) Punctuation (curly quotes, long dashes).
7) Double spaces.
8) URL tracking query parameters (e.g. utm_source, fbclid) (A).
9) Mixed-word Unicode homoglyphs (B).
10) Visual separator lines normalized to standard Org dividers (C).

Preserves:
- Org-mode structural metadata (#+TITLE, #+SUBTITLE, etc.)
- Cyrillic / non-English natural languages (Russian text left intact)
- Numbers like #1, #2
- URL hash fragments (e.g., http://link#bonus)
"""

import sys
import os
import re
import argparse
import unicodedata
import urllib.parse
import urllib.request
import emoji

# Emojis that carry useful semantic meaning (ratings, done status, warning warnings)
ALLOWED_EMOJIS = {"⭐", "🌟", "✅", "✔", "⚠️", "⚠"}

# Mapping for fancy bullets and symbols to standard ASCII representations
SYMBOL_MAPPING = {
    # Fancy bullets
    "○": "*",
    "🔹": "*",
    "🔸": "*",
    "•": "*",
    "◾": "*",
    "▪": "*",
    "∘": "*",
    "‣": "*",
    # Arrows / pointers
    "👉": "->",
    "➡": "->",
    "→": "->",
    "↳": "->",
    # Standardize punctuation
    "“": '"',
    "”": '"',
    "’": "'",
    "‘": "'",
    "—": "-",
    "–": "-",
    "‐": "-",
}

# Unwanted URL query parameters (typically tracking IDs)
UNWANTED_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "rcm", "ref", "source", "mc_cid", "mc_eid",
    "hss_channel", "spm", "click_id"
}

# Mixed-word Cyrillic to Latin homoglyph mappings
CYRILLIC_TO_LATIN = {
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y", "ѕ": "s", "і": "i",
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P",
    "Т": "T", "Х": "X", "У": "Y"
}

# Standalone hashtag detection line pattern:
# A line consisting only of hashtags and separator punctuation/whitespace
# e.g., "#AI #LLM" or "hashtag#machinelearning hashtag#deeplearning"
# We match optional leading whitespace/bullet, followed by repeating hashtag tokens
HASHTAG_TOKEN_PATTERN = re.compile(r"^(?:\s*|[*•-]\s*)*(?:(?:hashtag#|#)[a-zA-Z][a-zA-Z0-9_]*\s*)+$")


def is_standalone_hashtag_line(line: str) -> bool:
    """
    Returns True if the line consists entirely of hashtags and separators.
    """
    stripped = line.strip()
    if not stripped:
        return False
    # If the line is an Org-mode directive, do not treat it as hashtags
    if line.startswith("#+"):
        return False
    return bool(HASHTAG_TOKEN_PATTERN.match(stripped))


def clean_url(url: str) -> str:
    """
    Strips unwanted query parameters from a URL while keeping clean parameters.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return url
        qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        # Filter parameters, ignoring case of the parameter name
        cleaned_qsl = [(k, v) for k, v in qsl if k.lower() not in UNWANTED_PARAMS]
        if cleaned_qsl:
            new_query = urllib.parse.urlencode(cleaned_qsl)
        else:
            new_query = ""
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def resolve_short_url(url: str) -> str:
    """
    Attempts to resolve shortened URLs (e.g. from bit.ly, t.co) to their destinations.
    Skips if the domain is not a known shortener or if the request fails.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
        # Common shorteners
        if domain not in {"lnkd.in", "t.co", "bit.ly", "tinyurl.com", "ow.ly", "is.gd", "buff.ly"}:
            return url

        print(f"Resolving redirect for short URL: {url}...", file=sys.stderr)
        
        # HEAD request first
        req = urllib.request.Request(
            url, 
            method="HEAD", 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=2.5) as response:
            return response.geturl()
    except Exception:
        # Fallback to GET request in case HEAD is forbidden/unsupported
        try:
            req = urllib.request.Request(
                url, 
                method="GET", 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as response:
                return response.geturl()
        except Exception as e:
            # Fallback to original URL on final network failure/timeout
            print(f"Failed to resolve {url}: {e}", file=sys.stderr)
            return url


def clean_homoglyphs(text: str) -> str:
    """
    Normalizes mixed-script words (e.g., English words with accidental Cyrillic lookalikes).
    Leaves pure Cyrillic words (e.g. Russian text) completely untouched.
    """
    # Find word tokens containing Latin and/or Cyrillic chars
    words = re.findall(r"\b[a-zA-Z0-9\u0400-\u04ff]+\b", text)
    for word in words:
        # Count Latin/Cyrillic characters in this word
        latin_count = sum(1 for c in word if (65 <= ord(c) <= 90) or (97 <= ord(c) <= 122) or c.isdigit())
        cyrillic_count = sum(1 for c in word if 0x0400 <= ord(c) <= 0x04FF)
        
        # If the word is mixed, convert Cyrillic letters to Latin only if Latin is the dominant alphabet
        if latin_count > 0 and cyrillic_count > 0:
            if latin_count >= cyrillic_count:
                cleaned_word = "".join(CYRILLIC_TO_LATIN.get(c, c) for c in word)
                text = re.sub(r"\b" + re.escape(word) + r"\b", cleaned_word, text)
    return text


def clean_line_text(text: str, resolve_links: bool = False) -> str:
    """
    Core cleanup function for the text content of a line.
    """
    # 1. Map keycap ten (special codepoint) to standard "10"
    text = text.replace("\U0001f51f", "10")

    # 2. Normalize keycap digits (e.g. 5️⃣ -> 5)
    text = re.sub(r"(\d)(?:\ufe0f?\u20e3)", r"\1", text)

    # 3. Unicode NFKC normalization (converts mathematical bold/italic, ligatures, etc.)
    text = unicodedata.normalize("NFKC", text)

    # 4. Homoglyph cleaning for mixed Latin-Cyrillic words (B)
    text = clean_homoglyphs(text)

    # 5. Map fancy bullets and punctuation FIRST before filtering emojis
    # This prevents mapped emojis like 🔹 or 👉 from being deleted.
    for orig, replacement in SYMBOL_MAPPING.items():
        text = text.replace(orig, replacement)

    # 6. Clean emojis (keeping allowed ones)
    emoji_data = emoji.emoji_list(text)
    chars = list(text)
    # Iterate backwards to keep indices valid during removal
    for item in reversed(emoji_data):
        emoji_char = item["emoji"]
        if emoji_char not in ALLOWED_EMOJIS:
            start = item["match_start"]
            end = item["match_end"]
            chars[start:end] = []
    text = "".join(chars)

    # 7. Clean inline hashtags (e.g., #FeaturedFriday -> FeaturedFriday, hashtag#GenAI -> GenAI)
    # Avoids matching #1 (digits) and URL hashes (requires start of word or preceded by whitespace)
    text = re.sub(r"\bhashtag#([a-zA-Z][a-zA-Z0-9_]*)", r"\1", text)
    text = re.sub(r"(?<!\S)#([a-zA-Z][a-zA-Z0-9_]*)", r"\1", text)

    # 8. Clean URL parameters (A) and resolve redirects if requested
    url_pattern = re.compile(r"https?://[a-zA-Z0-9.\-_~:/?#\[\]@!$&'()*+,;=%]+")
    urls = url_pattern.findall(text)
    for url in set(urls):
        target_url = url
        if resolve_links:
            target_url = resolve_short_url(url)
        cleaned_url_str = clean_url(target_url)
        if cleaned_url_str != url:
            text = text.replace(url, cleaned_url_str)

    # 9. Collapse double spaces that are not leading spaces
    text = re.sub(r"(?<=\S) {2,}", " ", text)

    # 10. Remove spaces before standard punctuation
    text = re.sub(r" +([.,!?;:])", r"\1", text)

    return text.strip()


def clean_line(line: str, resolve_links: bool = False) -> str:
    """
    Cleans a single line while preserving Org-mode structure.
    """
    # Preserve org-mode metadata/comments (lines starting with #+ or # )
    if line.startswith("#+") or line.startswith("# "):
        return line

    # Preserve trailing newline char for formatting
    ending = ""
    if line.endswith("\n"):
        ending = "\n"
        line = line[:-1]

    # Standardize visual separators / horizontal rules to standard org divider "---" (C)
    if re.match(r"^[—–\-=\s._]{2,}$", line.strip()):
        stripped = line.strip()
        # Ensure it has at least one dash or equal sign and is mostly separator chars
        if len(stripped) >= 2 and any(c in "—–-=" for c in stripped) and all(c in "—–-=_." for c in stripped):
            return "---" + ending

    # If it is a standalone hashtag line, remove it completely
    if is_standalone_hashtag_line(line):
        return ""  # We return empty string (deleted)

    # Preserve and standardize list bullets (e.g., "  - item" or "🔹 Item")
    list_match = re.match(r"^(\s*)([-+*]|[○🔹🔸•◾▪∘‣])(\s+)(.*)$", line)
    if list_match:
        indent, bullet, sep, content = list_match.groups()
        cleaned_content = clean_line_text(content, resolve_links=resolve_links)
        # Standardize bullet: if it is a fancy bullet, map it.
        # If no indentation, map to '-' to avoid Org-mode treating it as a heading.
        if bullet in {"○", "🔹", "🔸", "•", "◾", "▪", "∘", "‣"}:
            bullet = "-" if not indent else "*"
        return indent + bullet + sep + cleaned_content + ending

    # Preserve Org-mode headlines structure (e.g., * Heading)
    headline_match = re.match(r"^(\*+\s+)(.*)$", line)
    if headline_match:
        prefix, content = headline_match.groups()
        cleaned_content = clean_line_text(content, resolve_links=resolve_links)
        return prefix + cleaned_content + ending

    # Standard text line
    return clean_line_text(line, resolve_links=resolve_links) + ending


def clean_file(file_path: str, out_path: str, resolve_links: bool = False) -> None:
    """
    Reads the file, cleans it line-by-line, removes redundant empty lines, and writes the output.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    cleaned_lines = []
    prev_was_empty = False

    for line in lines:
        cleaned = clean_line(line, resolve_links=resolve_links)
        is_empty = (cleaned.strip() == "")
        
        if is_empty:
            if not prev_was_empty:
                # Keep at most one empty line
                cleaned_lines.append("\n")
                prev_was_empty = True
        else:
            cleaned_lines.append(cleaned)
            prev_was_empty = False

    # Strip trailing empty lines from the file
    while cleaned_lines and cleaned_lines[-1] == "\n":
        cleaned_lines.pop()

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)


def run_tests():
    """
    Built-in self-test suite.
    """
    print("Running self-test suite...")

    tests = [
        # Fancy text normalization
        ("𝗣𝗼𝘁𝗲𝗻𝘁𝗶𝗮𝗹 𝗽𝗮𝗿𝗮𝗱𝗶𝗴𝗺 𝘀𝗵𝗶𝗳𝘁", "Potential paradigm shift"),
        ("2. 𝐀𝐠𝐞𝐧𝐭𝐬", "2. Agents"),
        ("It’s 𝗖𝗦𝟯𝟯𝟲: 𝗟𝗮𝗻𝗴𝘂𝗮𝗴𝗲 𝗠𝗼𝗱𝗲𝗹𝗶𝗻𝗴", "It's CS336: Language Modeling"),
        
        # Keycap digits
        ("1️⃣ First", "1 First"),
        ("5️⃣ Chain-of-Thought", "5 Chain-of-Thought"),
        ("🔟 Tenth item", "10 Tenth item"),
        
        # Emojis removal & whitelisting
        ("Free textbook! 📖✨", "Free textbook!"),
        ("Enjoy! 🌹", "Enjoy!"),
        ("3 ⭐ stars", "3 ⭐ stars"),
        ("Warning ⚠️ alert", "Warning ⚠️ alert"),
        ("Task ✅ completed", "Task ✅ completed"),
        ("Jailbreak 💪 ref", "Jailbreak ref"),
        
        # Hashtags
        ("#ArtificialIntelligence #DeepLearning", ""), # standalone
        ("This is #FeaturedFriday post", "This is FeaturedFriday post"), # inline
        ("It is not hashtag#GenAI!", "It is not GenAI!"), # inline hashtag
        ("Ranked #1 in class", "Ranked #1 in class"), # number hash preserved
        ("Go to https://github.com/org/repo#bonus-material", "Go to https://github.com/org/repo#bonus-material"), # URL fragment preserved
        
        # Fancy bullets and punctuation
        ("○ First bullet", "- First bullet"),
        ("🔹 Subpoint", "- Subpoint"),
        ("  🔹 Subpoint", "  * Subpoint"),
        ("📚 AI Engineering 👉 https://amzn.to", "AI Engineering -> https://amzn.to"),
        ("Let’s go — yes", "Let's go - yes"),
        
        # Org mode preservation
        ("#+TITLE: My Inbox", "#+TITLE: My Inbox"),
        ("#+begin_src python", "#+begin_src python"),
        
        # Space normalization
        ("Hello  world ! ", "Hello world!"),
        
        # A: URL parameter cleaning
        ("Click https://linkedin.com/posts/activity-123?utm_source=share&utm_medium=desktop&id=789", 
         "Click https://linkedin.com/posts/activity-123?id=789"),
         
        # B: Mixed-word homoglyphs
        ("The cаt is eating data.", "The cat is eating data."), # Cyrillic 'а' in 'cаt'
        ("Лори Сантос", "Лори Сантос"), # Pure Cyrillic preserved
        
        # C: Separators
        ("—-", "---"),
        ("—--", "---"),
        ("========", "---"),
    ]

    failed = 0
    for i, (input_text, expected) in enumerate(tests, 1):
        result = clean_line(input_text).rstrip("\n")
        if result != expected:
            print(f"Test {i} FAILED!")
            print(f"  Input:    {repr(input_text)}")
            print(f"  Expected: {repr(expected)}")
            print(f"  Got:      {repr(result)}")
            failed += 1
        else:
            print(f"Test {i} PASSED.")

    if failed == 0:
        print("All tests passed successfully!")
    else:
        print(f"{failed} tests failed.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Clean up copy-pasted social media news.")
    parser.add_argument("files", nargs="*", help="File paths to clean up.")
    parser.add_argument("--test", action="store_true", help="Run built-in self-tests.")
    parser.add_argument("--in-place", action="store_true", help="Modify files in place.")
    parser.add_argument("--backup", action="store_true", help="Create a backup file (.bak) before modifying in place.")
    parser.add_argument("--resolve-links", action="store_true", help="Attempt to resolve shortened links (may make slow network calls).")
    parser.add_argument("--out", help="Output path (only valid if single input file is provided).")

    args = parser.parse_args()

    if args.test or not args.files:
        run_tests()
        if not args.files:
            return

    for file_path in args.files:
        if not os.path.exists(file_path):
            print(f"Error: File {file_path} does not exist.")
            continue

        if args.in_place:
            out_path = file_path
            if args.backup:
                backup_path = file_path + ".bak"
                os.replace(file_path, backup_path)
                file_path = backup_path
        elif args.out:
            if len(args.files) > 1:
                print("Error: --out can only be used with a single input file.")
                sys.exit(1)
            out_path = args.out
        else:
            out_path = file_path + ".cleaned"

        print(f"Processing {file_path} -> {out_path}")
        clean_file(file_path, out_path, resolve_links=args.resolve_links)
        print(f"Successfully processed {file_path}.")


if __name__ == "__main__":
    main()

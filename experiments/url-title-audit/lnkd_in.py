"""How many of the audit's LinkedIn short links reached a destination and a title.

Reads the committed reports; no network. Run from the repository root:

    uv run python experiments/url-title-audit/lnkd_in.py
"""

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).parent
LINKEDIN_HOSTS = {"lnkd.in", "www.lnkd.in", "linkedin.com", "www.linkedin.com"}


def host(url: str | None) -> str | None:
    return urlparse(url).hostname if url else None


for name in ("report.json", "report-improved.json"):
    results = json.loads((HERE / name).read_text(encoding="utf-8"))["results"]
    short = [r for r in results if host(r["source_url"]) == "lnkd.in"]
    left = [r for r in short if host(r["final_url"]) not in LINKEDIN_HOSTS | {None}]
    titled = [r for r in short if r["title"]]
    print(
        f"{name}: {len(short)} of {len(results)} links are lnkd.in; "
        f"{len(left)} reached a destination off LinkedIn, {len(titled)} have a title"
    )

destinations = Counter(host(r["final_url"]) for r in left)
print("most common destinations:", ", ".join(f"{h} {n}" for h, n in destinations.most_common(6)))

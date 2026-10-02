"""Test queries: the 12 real inbox items plus 10 synthetic ones spanning the user's breadth."""

import re
from pathlib import Path

INBOX = Path.home() / "info-triage-inbox" / "info"


def item_query(d: Path, lead_words: int = 120) -> dict:
    text = (d / "index.md").read_text(encoding="utf-8")
    parts = text.split("---\n")
    fm = parts[1] if len(parts) > 2 else ""

    def field(name):
        m = re.search(rf"^{name}: (.*)$", fm, re.M)
        if not m:
            return ""
        v = m.group(1).strip().strip('"')
        return "" if v in ("null", "[]") else v

    title = field("title") or field("headline")
    lead = ""
    m = re.search(r"^## Lead\n(.*?)(?=\n## |\Z)", text, re.S | re.M)
    if m:
        lead = " ".join(w for w in re.sub(r"^> ?", "", m.group(1), flags=re.M).split())
    else:
        m = re.search(r"^## Captured\n(.*?)(?=\n## |\Z)", text, re.S | re.M)
        if m:
            lead = " ".join(w for w in re.sub(r"^> ?", "", m.group(1), flags=re.M).split())
    lead = " ".join(lead.split()[:lead_words])
    intent = field("intent")
    q = ". ".join(x for x in [title, intent, lead] if x)
    return {"id": d.name, "kind": field("kind"), "real": True, "query": q}


SYNTHETIC = [
    (
        "s1-zone-system",
        "photography",
        "Understanding the Zone System for digital exposure. A guide to previsualising tonal "
        "range, metering for the shadows and placing highlights, and how Ansel Adams' system "
        "translates to raw files and modern histogram-based exposure.",
    ),
    (
        "s2-chopin-fingering",
        "piano",
        "How to practise a Chopin nocturne: fingering choices, voicing the melody over the "
        "accompaniment, and pedalling. Slow practice strategies for the left-hand arpeggios.",
    ),
    (
        "s3-hsk-anki",
        "chinese",
        "Building an Anki deck for HSK 4 vocabulary. Spaced repetition scheduling, sentence "
        "cards versus word cards, and how many new characters per day is sustainable.",
    ),
    (
        "s4-soy-protein",
        "nutrition",
        "How much soy is too much on a vegan diet? Reviewing the evidence on isoflavones, "
        "daily protein targets, and which plant staples cover the amino acid profile.",
    ),
    (
        "s5-darienzo",
        "tango",
        "Dancing to Juan D'Arienzo: tango musicality, marking the strong beat, and how the "
        "orchestra's rhythmic style changes which figures actually fit the music.",
    ),
    (
        "s6-feed-ranking-interview",
        "ml-interview",
        "System design interview: design a news feed ranking system. Candidate generation, "
        "a two-tower retrieval model, feature stores, online serving latency, and how to "
        "evaluate the ranker with A/B tests.",
    ),
    (
        "s7-ab-peeking",
        "statistics",
        "Why you should not peek at A/B test results. Repeated significance testing inflates "
        "the false positive rate; sequential testing and always-valid p-values fix it.",
    ),
    (
        "s8-rust-ownership",
        "programming",
        "The Rust ownership model explained: moves, borrows, lifetimes, and why the borrow "
        "checker rejects code that looks correct. Worked examples with vectors and structs.",
    ),
    (
        "s9-vespa-restoration",
        "OUT-OF-SCOPE",
        "Restoring a 1960s Vespa scooter engine: sourcing piston rings, decarbonising the "
        "cylinder head, and rebuilding the two-stroke gearbox on a workbench at home.",
    ),
    (
        "s10-beekeeping",
        "OUT-OF-SCOPE",
        "Beekeeping for beginners: choosing between a Langstroth and a top-bar hive, "
        "installing your first package of bees, and treating for varroa mites in autumn.",
    ),
]


def all_items() -> list[dict]:
    out = []
    for d in sorted(INBOX.glob("2026-*"), key=lambda p: int(p.name.split("_")[1])):
        q = item_query(d)
        if "Test connection from Info Triage" in q["query"]:
            continue
        out.append(q)
    for sid, kind, text in SYNTHETIC:
        out.append({"id": sid, "kind": kind, "real": False, "query": text})
    return out


if __name__ == "__main__":
    for it in all_items():
        print(f"{it['id']:24s} {it['kind']:14s} {it['query'][:110]}")

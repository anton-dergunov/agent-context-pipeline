"""Build retrieval units from the Org plan repo and the Obsidian vault."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

# The same variables and defaults `info_triage/sync.py` reads.
NOTES = Path.home() / "Library/CloudStorage/Dropbox/notes"
ORG_ROOT = Path(os.environ.get("INFO_TRIAGE_ORG_ROOT") or NOTES / "org").expanduser()
VAULT_ROOT = Path(os.environ.get("INFO_TRIAGE_OBSIDIAN_ROOT") or NOTES / "obsidian").expanduser()

ORG_SKIP = {"workspace.org", "init.org"}
VAULT_SKIP_DIRS = {".git", ".obsidian", ".smart-env", ".trash", "image", "scripts",
                   "Templates", "Bases"}

KEYWORDS = {"TODO", "NEXT", "MAYB", "WAIT", "DONE", "STARTED", "INPR", "HOLD", "CANCELLED"}
HEADING = re.compile(r"^(\*+)\s+(.*)$")
TAGS = re.compile(r"\s+(:[A-Za-z0-9_@#%:]+:)\s*$")
MD_H = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Unit:
    kind: str          # org-task | org-section | org-charter | vault-section
    source: str        # "org" | "vault"
    file: str          # repo-relative path
    line: int          # 1-indexed start line
    path: str          # human breadcrumb
    title: str
    keyword: str | None
    text: str          # what gets embedded / indexed
    words: int


def _strip_tags(title: str) -> tuple[str, str]:
    m = TAGS.search(title)
    if not m:
        return title.strip(), ""
    return title[: m.start()].strip(), m.group(1)


def parse_org(path: Path, rel: str) -> list[Unit]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    units: list[Unit] = []

    # charter: everything before the first heading
    first = next((i for i, l in enumerate(lines) if HEADING.match(l)), len(lines))
    charter_lines = [l for l in lines[:first] if l.strip()]
    subtitle = next((l.split(":", 1)[1].strip() for l in charter_lines
                     if l.upper().startswith("#+SUBTITLE:")), "")
    title = next((l.split(":", 1)[1].strip() for l in charter_lines
                  if l.upper().startswith("#+TITLE:")), rel)
    prose = " ".join(l for l in charter_lines if not l.startswith("#+"))
    if prose or subtitle:
        body = f"{subtitle}. {prose}".strip()
        units.append(Unit("org-charter", "org", rel, 1, rel, title, None,
                          f"{title}. {body}", len(body.split())))

    # headings
    stack: list[str] = []
    cur: dict | None = None
    body: list[str] = []

    def flush():
        if cur is None:
            return
        raw = "\n".join(body).strip()
        # drop org drawers / scheduling noise from the embedded text
        keep = [l for l in raw.splitlines()
                if not re.match(r"^\s*(SCHEDULED:|DEADLINE:|CLOSED:|:PROPERTIES:|:END:|:[A-Z_]+:)", l)]
        btext = " ".join(l.strip() for l in keep).strip()
        crumb = " > ".join([rel] + cur["anc"])
        kind = "org-task" if cur["kw"] else "org-section"
        text = f"{crumb} > {cur['title']}. {btext}".strip()
        units.append(Unit(kind, "org", rel, cur["line"], crumb, cur["title"],
                          cur["kw"], text, len(btext.split())))

    for i, line in enumerate(lines[first:], start=first + 1):
        m = HEADING.match(line)
        if not m:
            if cur is not None:
                body.append(line)
            continue
        flush()
        level = len(m.group(1))
        rest = m.group(2)
        kw = None
        parts = rest.split(None, 1)
        if parts and parts[0] in KEYWORDS:
            kw = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
        rest = re.sub(r"^\[#[A-C]\]\s*", "", rest)
        htitle, _ = _strip_tags(rest)
        del stack[level - 1:]
        cur = {"line": i, "title": htitle, "kw": kw, "anc": list(stack)}
        stack.append(htitle)
        body = []
    flush()
    return units


def parse_vault(path: Path, rel: str, max_words: int = 280) -> list[Unit]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    note = path.stem
    folder = str(Path(rel).parent)
    units: list[Unit] = []

    # sections split on any ## / ### heading
    marks = [(0, note)]
    for i, l in enumerate(lines):
        m = MD_H.match(l)
        if m and len(m.group(1)) <= 3:
            marks.append((i, m.group(2).strip()))
    marks.append((len(lines), ""))

    for (start, heading), (end, _) in zip(marks, marks[1:]):
        seg = lines[start:end]
        body = " ".join(l.strip() for l in seg
                        if l.strip() and not MD_H.match(l) and not l.startswith("---"))
        if not body:
            continue
        crumb = f"{folder}/{note}" + (f" > {heading}" if heading != note else "")
        words = body.split()
        # window very long sections so a chunk stays inside the encoder's context.
        # each window keeps the line it actually starts on, so two chunks of one
        # section never collapse onto the same citation
        body_lines = [j for j, l in enumerate(seg)
                      if l.strip() and not MD_H.match(l) and not l.startswith("---")]
        seen = 0
        starts = []
        for j in body_lines:
            starts.append((seen, start + j + 1))
            seen += len(seg[j].split())
        for w0 in range(0, len(words), max_words):
            chunk = " ".join(words[w0: w0 + max_words])
            line = next((ln for c, ln in reversed(starts) if c <= w0), start + 1)
            units.append(Unit("vault-section", "vault", rel, line, crumb, heading,
                              None, f"{crumb}. {chunk}", len(chunk.split())))
    return units


def build() -> list[Unit]:
    units: list[Unit] = []
    for p in sorted(ORG_ROOT.rglob("*.org")):
        if ".git" in p.parts or p.name in ORG_SKIP:
            continue
        units += parse_org(p, str(p.relative_to(ORG_ROOT)))
    for p in sorted(VAULT_ROOT.rglob("*.md")):
        if any(d in p.parts for d in VAULT_SKIP_DIRS):
            continue
        units += parse_vault(p, str(p.relative_to(VAULT_ROOT)))
    return units


if __name__ == "__main__":
    us = build()
    out = Path(__file__).with_name("units.jsonl")
    with out.open("w") as fh:
        for u in us:
            fh.write(json.dumps(asdict(u)) + "\n")
    from collections import Counter
    print(f"{len(us)} units -> {out}")
    print(Counter(u.kind for u in us))
    print("median words", sorted(u.words for u in us)[len(us) // 2])
    print("total words", sum(u.words for u in us))

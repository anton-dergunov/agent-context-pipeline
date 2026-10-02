"""Research-provider URL normalization and metadata parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup


@dataclass(frozen=True, slots=True)
class BodyCandidate:
    kind: str
    url: str


@dataclass(frozen=True, slots=True)
class ResearchReference:
    source_url: str
    provider: str
    paper_id: str
    canonical_url: str
    metadata_urls: tuple[str, ...]
    body_candidates: tuple[BodyCandidate, ...]


ARXIV_ID = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$",
    re.IGNORECASE,
)


def _arxiv(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "arxiv.org":
        return None
    match = re.match(r"^/(?:abs|pdf|html)/(.+?)/?$", parsed.path, re.IGNORECASE)
    if not match:
        return None
    paper_id = match.group(1)
    if paper_id.lower().endswith(".pdf"):
        paper_id = paper_id[:-4]
    if not ARXIV_ID.fullmatch(paper_id):
        return None
    base = f"https://arxiv.org/abs/{paper_id}"
    return ResearchReference(
        value,
        "arxiv",
        paper_id,
        base,
        (base,),
        (
            BodyCandidate("html", f"https://arxiv.org/html/{paper_id}"),
            BodyCandidate("pdf", f"https://arxiv.org/pdf/{paper_id}"),
        ),
    )


def _acl(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"aclanthology.org", "aclweb.org"}:
        return None
    path = parsed.path
    if host == "aclweb.org" and path.startswith("/anthology/"):
        path = path.removeprefix("/anthology")
    paper_id = path.strip("/")
    if paper_id.lower().endswith(".pdf"):
        paper_id = paper_id[:-4]
    if not paper_id or "/" in paper_id or not re.fullmatch(r"[A-Za-z0-9.-]+", paper_id):
        return None
    page = f"https://aclanthology.org/{paper_id}/"
    return ResearchReference(
        value,
        "acl",
        paper_id,
        page,
        (page,),
        (BodyCandidate("pdf", f"https://aclanthology.org/{paper_id}.pdf"),),
    )


def _pmlr(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "proceedings.mlr.press":
        return None
    match = re.match(r"^/(v\d+)/([^/]+)(?:\.html|/\2\.pdf)/?$", parsed.path)
    if not match:
        return None
    volume, paper_id = match.groups()
    page = f"https://proceedings.mlr.press/{volume}/{paper_id}.html"
    return ResearchReference(
        value,
        "pmlr",
        f"{volume}-{paper_id}",
        page,
        (page,),
        (
            BodyCandidate(
                "pdf", f"https://proceedings.mlr.press/{volume}/{paper_id}/{paper_id}.pdf"
            ),
        ),
    )


def _openreview(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "openreview.net":
        return None
    query = parse_qs(parsed.query)
    paper_id = (query.get("id") or [None])[0]
    if not paper_id and query.get("redirect"):
        nested = unquote(query["redirect"][0])
        nested_query = parse_qs(urlsplit(nested).query)
        paper_id = (nested_query.get("id") or [None])[0]
    if not paper_id or parsed.path not in {"/forum", "/pdf", "/challenge"}:
        return None
    page = f"https://openreview.net/forum?id={paper_id}"
    return ResearchReference(
        value,
        "openreview",
        paper_id,
        page,
        (
            f"https://api2.openreview.net/notes?id={paper_id}",
            f"https://api.openreview.net/notes?id={paper_id}",
        ),
        (BodyCandidate("pdf", f"https://openreview.net/pdf?id={paper_id}"),),
    )


def _cvf(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "openaccess.thecvf.com":
        return None
    path = parsed.path
    match = re.match(r"^(/content/[^/]+)/(html|papers)/(.+?)\.(?:html|pdf)$", path)
    if not match:
        return None
    root, _, stem = match.groups()
    paper_id = f"{root.strip('/').replace('/', '-')}-{stem}"
    page = f"https://openaccess.thecvf.com{root}/html/{stem}.html"
    pdf = f"https://openaccess.thecvf.com{root}/papers/{stem}.pdf"
    return ResearchReference(value, "cvf", paper_id, page, (page,), (BodyCandidate("pdf", pdf),))


def _neurips(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "proceedings.neurips.cc":
        return None
    path = parsed.path
    if not path.lower().endswith((".html", ".pdf")):
        return None
    filename = path.rsplit("/", 1)[-1]
    if "-Abstract" in filename:
        page_path = path
        pdf_path = path.replace("-Abstract", "-Paper").removesuffix(".html") + ".pdf"
        if "/paper_files/paper/" in pdf_path:
            pdf_path = pdf_path.replace("/hash/", "/file/")
    elif "-Paper" in filename:
        pdf_path = path
        page_path = path.replace("-Paper", "-Abstract").removesuffix(".pdf") + ".html"
        if "/paper_files/paper/" in page_path:
            page_path = page_path.replace("/file/", "/hash/")
    else:
        return None
    paper_id = filename.split("-", 1)[0]
    page = f"https://proceedings.neurips.cc{page_path}"
    pdf = f"https://proceedings.neurips.cc{pdf_path}"
    return ResearchReference(
        value, "neurips", paper_id, page, (page,), (BodyCandidate("pdf", pdf),)
    )


def _jmlr(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "jmlr.org":
        return None
    page_match = re.match(r"^/papers/v(\d+)/([^/]+)\.html$", parsed.path)
    pdf_match = re.match(r"^/papers/volume(\d+)/([^/]+)/\2\.pdf$", parsed.path)
    match = page_match or pdf_match
    if not match:
        return None
    volume, paper_id = match.groups()
    page = f"https://www.jmlr.org/papers/v{volume}/{paper_id}.html"
    pdf = f"https://www.jmlr.org/papers/volume{volume}/{paper_id}/{paper_id}.pdf"
    return ResearchReference(
        value,
        "jmlr",
        f"v{volume}-{paper_id}",
        page,
        (page,),
        (BodyCandidate("pdf", pdf),),
    )


def _researchgate(value: str) -> ResearchReference | None:
    parsed = urlsplit(value)
    if (parsed.hostname or "").lower().removeprefix("www.") != "researchgate.net":
        return None
    match = re.match(r"^/publication/(\d+)(?:_|/|$)", parsed.path)
    if not match:
        return None
    paper_id = match.group(1)
    canonical = f"https://www.researchgate.net{parsed.path.rstrip('/')}"
    return ResearchReference(value, "researchgate", paper_id, canonical, (canonical,), ())


MATCHERS = (_arxiv, _acl, _pmlr, _openreview, _cvf, _neurips, _jmlr, _researchgate)


def match_research_url(value: str) -> ResearchReference | None:
    """Return a normalized provider reference when a research URL is recognized."""
    for matcher in MATCHERS:
        if reference := matcher(value.strip()):
            return reference
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _meta_values(soup: BeautifulSoup, name: str) -> list[str]:
    values: list[str] = []
    for tag in soup.find_all("meta"):
        key = str(tag.get("name") or tag.get("property") or "").lower()
        if key == name.lower() and (value := _clean(tag.get("content"))):
            values.append(value)
    return values


def _first_meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        values = _meta_values(soup, name)
        if values:
            return values[0]
    return None


def _json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            value = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict) and any(
                key in candidate for key in ("headline", "name", "abstract")
            ):
                return candidate
    return {}


def _page_abstract(soup: BeautifulSoup) -> str | None:
    for selector in (
        "div#abstract",
        "section#abstract",
        ".acl-abstract",
        "div.abstract",
        "p.abstract",
    ):
        if node := soup.select_one(selector):
            value = _clean(node.get_text(" ", strip=True))
            if value:
                return re.sub(r"^Abstract\s*[:—-]\s*", "", value, flags=re.IGNORECASE)
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if _clean(heading.get_text(" ", strip=True)).casefold() != "abstract":
            continue
        for sibling in heading.find_next_siblings():
            if sibling.name and re.fullmatch(r"h[1-6]", sibling.name):
                break
            if value := _clean(sibling.get_text(" ", strip=True)):
                return value
    return None


def parse_html_metadata(reference: ResearchReference, html: str, final_url: str) -> dict[str, Any]:
    """Normalize Highwire/JSON-LD metadata and provider-specific public fields."""
    soup = BeautifulSoup(html, "html.parser")
    structured = _json_ld(soup)
    authors = _meta_values(soup, "citation_author")
    if not authors:
        raw_authors = structured.get("author") or []
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        authors = [
            value
            for item in raw_authors
            if (value := _clean(item.get("name") if isinstance(item, dict) else item))
        ]
    title = _first_meta(soup, "citation_title", "og:title") or _clean(
        structured.get("headline") or structured.get("name")
    )
    abstract = (
        _first_meta(soup, "citation_abstract")
        or _clean(structured.get("abstract"))
        or _page_abstract(soup)
    )

    provider_fields: dict[str, Any] = {}
    arxiv_subjects: list[str] = []
    arxiv_doi: str | None = None
    arxiv_license: str | None = None
    if reference.provider == "arxiv":
        if node := soup.select_one("h1.title"):
            title = _clean(node.get_text(" ", strip=True).removeprefix("Title:")) or title
        selected_authors = [
            _clean(node.get_text(" ", strip=True)) for node in soup.select("div.authors a")
        ]
        authors = [value for value in selected_authors if value] or authors
        if node := soup.select_one("blockquote.abstract"):
            abstract = _clean(node.get_text(" ", strip=True).removeprefix("Abstract:")) or abstract
        if node := soup.select_one("div.submission-history"):
            provider_fields["submission_history"] = _clean(node.get_text(" ", strip=True))
        if node := soup.select_one("td.subjects"):
            subjects_text = _clean(node.get_text(" ", strip=True))
            provider_fields["subjects"] = subjects_text
            arxiv_subjects = [
                value for part in (subjects_text or "").split(";") if (value := _clean(part))
            ]
        for row in soup.select("table.metatable tr"):
            label = (
                _clean(row.find("td", class_="tablecell label").get_text(" ", strip=True))
                if row.find("td", class_="tablecell label")
                else None
            )
            value_node = row.find("td", class_="tablecell comments")
            if label and value_node:
                provider_fields[label.rstrip(":").lower().replace(" ", "_")] = _clean(
                    value_node.get_text(" ", strip=True)
                )
        if node := soup.select_one("#arxiv-doi-link"):
            arxiv_doi = _clean(node.get_text(" ", strip=True))
            if arxiv_doi and "doi.org/" in arxiv_doi:
                arxiv_doi = arxiv_doi.split("doi.org/", 1)[1]
        if node := soup.select_one("div.abs-license a[href]"):
            arxiv_license = urljoin(final_url, str(node["href"]))

    canonical = reference.canonical_url
    if reference.provider != "arxiv":
        if link := soup.find("link", rel=lambda value: value and "canonical" in value):
            if link.get("href"):
                canonical = urljoin(final_url, str(link["href"]))
    pdf_url = _first_meta(soup, "citation_pdf_url")
    keywords = _meta_values(soup, "citation_keywords")
    if not keywords and isinstance(structured.get("keywords"), str):
        keywords = [item.strip() for item in structured["keywords"].split(",") if item.strip()]
    first_page = _first_meta(soup, "citation_firstpage")
    last_page = _first_meta(soup, "citation_lastpage")
    pages = (
        f"{first_page}-{last_page}"
        if first_page and last_page and first_page != last_page
        else first_page or last_page
    )
    return {
        "schema_version": 1,
        "kind": "research",
        "provider": reference.provider,
        "paper_id": reference.paper_id,
        "requested_url": reference.source_url,
        "canonical_url": canonical,
        "title": title,
        "authors": authors,
        "abstract": abstract or _first_meta(soup, "description", "og:description"),
        "submitted_at": _first_meta(soup, "citation_date", "citation_online_date"),
        "published_at": _first_meta(soup, "citation_publication_date", "article:published_time")
        or _clean(structured.get("datePublished")),
        "updated_at": _clean(structured.get("dateModified")),
        "subjects": arxiv_subjects or keywords,
        "venue": _first_meta(
            soup, "citation_conference_title", "citation_journal_title", "citation_inbook_title"
        ),
        "doi": _first_meta(soup, "citation_doi") or arxiv_doi,
        "volume": _first_meta(soup, "citation_volume"),
        "pages": pages,
        "publisher": _first_meta(soup, "citation_publisher"),
        "license": _first_meta(soup, "citation_license")
        or _clean(structured.get("license"))
        or arxiv_license,
        "pdf_url": urljoin(final_url, pdf_url) if pdf_url else None,
        "provider_metadata": {key: value for key, value in provider_fields.items() if value},
    }


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    if value > 10_000_000_000:
        value /= 1000
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def parse_openreview_metadata(
    reference: ResearchReference, payload: dict[str, Any]
) -> dict[str, Any]:
    notes = payload.get("notes") or []
    if not notes or not isinstance(notes[0], dict):
        raise ValueError("OpenReview returned no matching note")
    note = notes[0]
    content = note.get("content") or {}

    def field(name: str, default: Any = None) -> Any:
        value = content.get(name, default)
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    authors = field("authors", [])
    if isinstance(authors, str):
        authors = [authors]
    keywords = field("keywords", [])
    if isinstance(keywords, str):
        keywords = [item.strip() for item in keywords.split(",") if item.strip()]
    pdf = field("pdf")
    return {
        "schema_version": 1,
        "kind": "research",
        "provider": "openreview",
        "paper_id": reference.paper_id,
        "requested_url": reference.source_url,
        "canonical_url": reference.canonical_url,
        "title": _clean(field("title")),
        "authors": [_clean(value) for value in authors if _clean(value)],
        "abstract": _clean(field("abstract")),
        "submitted_at": _timestamp(note.get("cdate")),
        "published_at": _timestamp(note.get("pdate")),
        "updated_at": _timestamp(note.get("mdate")),
        "subjects": keywords,
        "venue": _clean(field("venue") or field("venueid")),
        "doi": _clean(field("doi")),
        "volume": None,
        "pages": None,
        "publisher": None,
        "license": _clean(field("license")),
        "pdf_url": urljoin("https://openreview.net", str(pdf)) if pdf else None,
        "provider_metadata": {
            "forum": note.get("forum"),
            "invitation": note.get("invitation"),
        },
    }

#!/usr/bin/env python3
"""Replace URLs in a text file with their final destinations.

The resolver follows ordinary HTTP redirects and a small set of deterministic
HTML/URL redirect mechanisms.  In particular, it understands the external-link
interstitial currently returned by LinkedIn's lnkd.in shortener.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


URL_RE = re.compile(r"https?://[^\s<>\"\]]+", re.IGNORECASE)
META_REFRESH_URL_RE = re.compile(r"(?:^|;)\s*url\s*=\s*(.+?)\s*$", re.IGNORECASE)
JS_REDIRECT_RES = (
    re.compile(
        r"(?:window\s*\.\s*)?location(?:\s*\.\s*href)?\s*=\s*"
        r"(?P<quote>['\"])(?P<url>.+?)(?P=quote)\s*;?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:window\s*\.\s*)?location\s*\.\s*replace\s*\(\s*"
        r"(?P<quote>['\"])(?P<url>.+?)(?P=quote)\s*\)\s*;?\s*",
        re.IGNORECASE,
    ),
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

# Redirectors which put an already usable destination in a query parameter.
# Decoding these first avoids a network call and works when the redirector is
# blocked by authentication or bot protection.
QUERY_REDIRECTORS: dict[str, tuple[str, ...]] = {
    "l.facebook.com": ("u",),
    "lm.facebook.com": ("u",),
    "www.google.com": ("q", "url"),
    "google.com": ("q", "url"),
    "www.youtube.com": ("q",),
    "youtube.com": ("q",),
    "link.zhihu.com": ("target",),
    "steamcommunity.com": ("url",),
    "away.vk.com": ("to",),
    "www.linkedin.com": ("url",),
    "linkedin.com": ("url",),
}

# Common public and branded shorteners.  Resolving only these by default avoids
# rewriting ordinary URLs which happen to redirect to cookie, login, locale, or
# moved-content pages.  --all is available for discovery and unusual domains.
SHORTENER_HOSTS = frozenset(
    {
        "1url.com",
        "2.gp",
        "2pl.us",
        "3.ly",
        "4sq.com",
        "7.ly",
        "9qr.de",
        "a.co",
        "adf.ly",
        "aka.ms",
        "amzn.eu",
        "amzn.to",
        "aol.it",
        "apple.co",
        "b.link",
        "bbc.in",
        "bc.vc",
        "bit.do",
        "bit.ly",
        "bitly.com",
        "bl.ink",
        "brlnk.io",
        "buff.ly",
        "cli.re",
        "cnn.it",
        "cos.lv",
        "cutt.ly",
        "cutt.us",
        "db.tt",
        "detne.ws",
        "dlvr.it",
        "econ.st",
        "eepurl.com",
        "fb.me",
        "flip.it",
        "forms.gle",
        "fur.ly",
        "g.co",
        "gg.gg",
        "git.io",
        "go2l.ink",
        "goo.gl",
        "goo.gle",
        "ht.ly",
        "hubs.la",
        "hubs.li",
        "huff.to",
        "ift.tt",
        "is.gd",
        "ity.im",
        "j.mp",
        "lat.ms",
        "lc.chat",
        "lnk.bio",
        "lnk.to",
        "lnkd.in",
        "mcaf.ee",
        "mzl.la",
        "n.pr",
        "nyer.cm",
        "nyti.ms",
        "on.freep.com",
        "ow.ly",
        "p4k.in",
        "qr.ae",
        "rb.gy",
        "read.bi",
        "rebrand.ly",
        "rol.st",
        "s.id",
        "say.ly",
        "shar.es",
        "shiny.link",
        "short.io",
        "shorturl.at",
        "shrtco.de",
        "snip.ly",
        "soo.gd",
        "spoti.fi",
        "stanford.io",
        "t.co",
        "t.ly",
        "thebea.st",
        "therin.gr",
        "tiny.cc",
        "tiny.one",
        "tinyurl.com",
        "tmblr.co",
        "trib.in",
        "v.gd",
        "wa.link",
        "wapo.st",
        "wp.me",
        "x.co",
        "yousend.it",
        "youtu.be",
    }
)
SHORTENER_HOST_SUFFIXES = (".trib.al", ".safelinks.protection.outlook.com")


@dataclass(frozen=True)
class Resolution:
    url: str
    succeeded: bool
    error: str | None = None


def make_session(retries: int) -> requests.Session:
    """Return a session with conservative retries for transient failures."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        backoff_factor=1.0,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.max_redirects = 20
    return session


def _decoded_http_url(value: str) -> str | None:
    """Decode a URL-valued field without interpreting arbitrary page text."""
    candidate = html.unescape(value).strip().strip("'\"")
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    if urlsplit(candidate).scheme.lower() in {"http", "https"}:
        return candidate
    return None


def embedded_destination(url: str) -> str | None:
    """Extract destinations embedded by well-known outbound redirectors."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()

    # href.li uses the entire query string as its target, without a key.
    if host == "href.li" and parsed.query:
        return _decoded_http_url(parsed.query)

    # Microsoft Safe Links is deployed on many regional subdomains.
    if host.endswith(".safelinks.protection.outlook.com"):
        values = parse_qs(parsed.query).get("url", ())
        return _decoded_http_url(values[0]) if values else None

    keys = QUERY_REDIRECTORS.get(host)
    if not keys:
        return None

    # Restrict broad hosts to their actual redirect endpoints.
    if host in {"www.google.com", "google.com"} and parsed.path != "/url":
        return None
    if host in {"l.facebook.com", "lm.facebook.com"} and parsed.path != "/l.php":
        return None
    if host in {"www.youtube.com", "youtube.com"} and parsed.path != "/redirect":
        return None
    if host in {"www.linkedin.com", "linkedin.com"} and parsed.path != "/safety/go":
        return None

    query = parse_qs(parsed.query)
    for key in keys:
        values = query.get(key, ())
        if values:
            destination = _decoded_http_url(values[0])
            if destination:
                return destination
    return None


def is_redirector_url(url: str) -> bool:
    """Return whether URL should be resolved in the safe default mode."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in SHORTENER_HOSTS or any(
        host == suffix[1:] or host.endswith(suffix) for suffix in SHORTENER_HOST_SUFFIXES
    ):
        return True
    return embedded_destination(url) is not None


def _normalized_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _read_response_body(response: requests.Response, limit: int) -> bytes:
    """Read at most limit bytes so a URL cannot force a huge download."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        remaining = limit - size
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        size += min(len(chunk), remaining)
        if size >= limit:
            break
    return b"".join(chunks)


def _html_destination(body: bytes, response: requests.Response) -> str | None:
    """Extract a destination from supported deterministic HTML redirects."""
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type and not body.lstrip().lower().startswith(b"<!doctype html"):
        return None

    encoding = response.encoding or "utf-8"
    text = body.decode(encoding, errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    host = (urlsplit(response.url).hostname or "").lower()

    # lnkd.in now returns a safety interstitial instead of an HTTP redirect.
    # This semantic attribute is substantially less brittle than CSS classes or
    # visible text, and it does not use the title surrounding the original URL.
    if host == "lnkd.in" or host.endswith(".linkedin.com"):
        link = soup.select_one(
            'a[data-tracking-control-name="external_url_click"][href]'
        )
        if link:
            return urljoin(response.url, html.unescape(str(link["href"])))

    for meta in soup.find_all("meta"):
        equiv = meta.get("http-equiv")
        content = meta.get("content")
        if not equiv or not content or str(equiv).lower() != "refresh":
            continue
        match = META_REFRESH_URL_RE.search(str(content))
        if match:
            return urljoin(
                response.url,
                html.unescape(match.group(1).strip().strip("'\"")),
            )

    # Some legacy shorteners use a literal JavaScript navigation.  Only parse
    # direct string assignments/calls; never execute JavaScript.
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text(" ")
        for pattern in JS_REDIRECT_RES:
            # Requiring the entire script element to be a navigation prevents
            # false positives from ordinary application code and examples.
            match = pattern.fullmatch(script_text.strip())
            if match:
                return urljoin(response.url, html.unescape(match.group("url")))

    return None


def resolve_url(
    url: str,
    session: requests.Session,
    *,
    timeout: float,
    max_html_bytes: int,
    max_rounds: int = 10,
) -> Resolution:
    """Resolve one URL through network and page-level redirect mechanisms."""
    original = html.unescape(url)
    current = original
    seen: set[str] = set()

    try:
        for _ in range(max_rounds):
            if current in seen:
                return Resolution(current, False, "redirect loop detected")
            seen.add(current)

            embedded = embedded_destination(current)
            if embedded and embedded not in seen:
                current = embedded
                continue

            with session.get(
                current,
                allow_redirects=True,
                timeout=(min(timeout, 15.0), timeout),
                stream=True,
            ) as response:
                network_url = response.url
                body = _read_response_body(response, max_html_bytes)
                page_url = _html_destination(body, response)

            next_url = page_url or network_url
            if next_url == current:
                if is_redirector_url(current):
                    partial = (
                        current
                        if _normalized_host(current) != _normalized_host(original)
                        else original
                    )
                    return Resolution(
                        partial,
                        False,
                        "shortener chain did not expose a final destination "
                        "(the last link may be expired)",
                    )
                return Resolution(current, True)
            scheme = urlsplit(next_url).scheme.lower()
            if scheme and scheme not in {"http", "https"}:
                return Resolution(next_url, True)
            current = next_url

        return Resolution(current, False, f"exceeded {max_rounds} page-level redirects")
    except requests.RequestException as exc:
        # Preserve a target extracted during an earlier page-level/embedded hop,
        # even if fetching that target fails. Requests does not expose an HTTP
        # redirect target when it raises while following the redirect itself.
        partial = current if current != original else original
        return Resolution(partial, False, str(exc))


def _split_url_and_punctuation(match: re.Match[str]) -> tuple[str, str]:
    """Keep prose/Markdown punctuation outside the URL replacement."""
    value = match.group(0)
    suffix = ""
    while value and value[-1] in ".,;:!?'":
        suffix = value[-1] + suffix
        value = value[:-1]
    while value.endswith(")") and value.count(")") > value.count("("):
        suffix = ")" + suffix
        value = value[:-1]
    while value.endswith("}") and value.count("}") > value.count("{"):
        suffix = "}" + suffix
        value = value[:-1]
    return value, suffix


def iter_urls(text: str) -> Iterable[str]:
    for match in URL_RE.finditer(text):
        url, _ = _split_url_and_punctuation(match)
        if url:
            yield url


def replace_urls(
    text: str,
    resolver: "FileResolver",
) -> str:
    def replace(match: re.Match[str]) -> str:
        url, suffix = _split_url_and_punctuation(match)
        return resolver.resolve(url) + suffix

    return URL_RE.sub(replace, text)


class FileResolver:
    def __init__(
        self,
        *,
        timeout: float,
        retries: int,
        max_html_bytes: int,
        cache: dict[str, str] | None = None,
        verbose: bool = False,
        resolve_all: bool = False,
    ) -> None:
        self.timeout = timeout
        self.max_html_bytes = max_html_bytes
        self.cache = cache if cache is not None else {}
        self.verbose = verbose
        self.resolve_all = resolve_all
        self.session = make_session(retries)
        self.failures: list[tuple[str, str]] = []

    def resolve(self, url: str) -> str:
        if not self.resolve_all and not is_redirector_url(url):
            return url
        if url in self.cache:
            cached = self.cache[url]
            # Do not perpetuate a stale/partial cache entry which still points
            # at a recognized shortener; retry it instead.
            if not is_redirector_url(cached):
                return cached
        result = resolve_url(
            url,
            self.session,
            timeout=self.timeout,
            max_html_bytes=self.max_html_bytes,
        )
        if result.succeeded:
            self.cache[url] = result.url
            if self.verbose and result.url != url:
                print(f"{url} -> {result.url}", file=sys.stderr)
        else:
            message = result.error or "unknown resolution error"
            self.failures.append((url, message))
            print(f"warning: could not resolve {url}: {message}", file=sys.stderr)
        return result.url


def load_cache(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError(f"cache must be a JSON object mapping URLs to URLs: {path}")
    return data


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 text file to process")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("-o", "--output", type=Path, help="write to this file")
    destination.add_argument(
        "-i", "--in-place", action="store_true", help="atomically replace the input file"
    )
    parser.add_argument(
        "--cache", type=Path, help="optional persistent JSON cache (read and update)"
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="read timeout in seconds")
    parser.add_argument("--retries", type=int, default=4, help="transient network retries")
    parser.add_argument(
        "--max-html-bytes",
        type=int,
        default=2 * 1024 * 1024,
        help="maximum response bytes inspected for page redirects",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show resolved URLs")
    parser.add_argument(
        "--all",
        action="store_true",
        help="follow every URL, including hosts not recognized as shorteners",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero if any selected URL could not be fully resolved",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if args.max_html_bytes <= 0:
        parser.error("--max-html-bytes must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    text = args.input.read_text(encoding="utf-8")
    cache = load_cache(args.cache)
    resolver = FileResolver(
        timeout=args.timeout,
        retries=args.retries,
        max_html_bytes=args.max_html_bytes,
        cache=cache,
        verbose=args.verbose,
        resolve_all=args.all,
    )
    output = replace_urls(text, resolver)

    if args.in_place:
        atomic_write(args.input, output)
    elif args.output:
        atomic_write(args.output, output)
    else:
        sys.stdout.write(output)

    if args.cache is not None:
        atomic_write(
            args.cache,
            json.dumps(resolver.cache, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
    return 1 if args.strict and resolver.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

import json

from info_triage.extractors.medium.direct import (
    DirectClient,
    body_model_to_markdown,
    load_cookie_file,
)
from info_triage.extractors.medium.urls import parse_article_url


def _payload(*, locked=True):
    return {
        "success": True,
        "payload": {
            "value": {
                "id": "abcdef123456",
                "title": "A useful story",
                "creatorId": "author-id",
                "mediumUrl": "https://medium.com/example/a-useful-story-abcdef123456",
                "webCanonicalUrl": "https://medium.com/example/a-useful-story-abcdef123456",
                "firstPublishedAt": 1_700_000_000_000,
                "updatedAt": 1_700_000_001_000,
                "virtuals": {"tags": [{"slug": "testing"}]},
                "content": {
                    "isLockedPreviewOnly": locked,
                    "bodyModel": {
                        "paragraphs": [
                            {"type": 3, "text": "A useful story", "markups": []},
                            {
                                "type": 1,
                                "text": "Opening paragraph with a source.",
                                "markups": [
                                    {
                                        "type": 3,
                                        "start": 25,
                                        "length": 6,
                                        "href": "https://example.com",
                                    }
                                ],
                            },
                            {"type": 13, "text": "Next section", "markups": []},
                            {"type": 6, "text": "Quoted text", "markups": []},
                            {"type": 8, "text": "print('hello')", "markups": []},
                        ]
                    },
                },
            },
            "references": {"User": {"author-id": {"name": "An Author", "username": "an-author"}}},
        },
    }


class _Response:
    def __init__(self, content, content_type, url):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.status_code = 200
        self.url = url

    @property
    def text(self):
        return self.content.decode()


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        if "format=json" in url:
            content = ("]) }while(1);</x>".replace(" ", "") + json.dumps(self.payload)).encode()
            return _Response(content, "application/json", url)
        html = b"<html><body><article><h1>A useful story</h1><p>Short page.</p></article></body></html>"
        return _Response(html, "text/html", url)


def test_body_model_to_markdown_preserves_basic_structure():
    body = _payload()["payload"]["value"]["content"]["bodyModel"]
    markdown = body_model_to_markdown(body)
    assert markdown.startswith("# A useful story")
    assert "[source](https://example.com)" in markdown
    assert "## Next section" in markdown
    assert "> Quoted text" in markdown
    assert "```\nprint('hello')\n```" in markdown


def test_direct_client_uses_structured_preview_and_author_metadata():
    client = DirectClient()
    session = _Session(_payload())
    client._session = session
    reference = parse_article_url("https://medium.com/example/a-useful-story-abcdef123456")
    article = client.get_article(reference)
    assert article.availability == "preview"
    assert article.source == "medium_json"
    assert article.author == "An Author"
    assert article.username == "an-author"
    assert article.tags == ("testing",)
    assert len(session.urls) == 2


def test_loads_playwright_cookie_json(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "sid",
                        "value": "secret-not-logged",
                        "domain": ".medium.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    }
                ]
            }
        )
    )
    jar = load_cookie_file(path)
    cookies = list(jar)
    assert [(item.name, item.domain) for item in cookies] == [("sid", ".medium.com")]

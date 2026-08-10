import json

import pytest

from linkedin_extractor.parser import ParseError, parse_post
from linkedin_extractor.urls import parse_post_url


def _page() -> str:
    structured = {
        "@context": "https://schema.org",
        "@type": "SocialMediaPosting",
        "@id": "https://www.linkedin.com/posts/person_example-activity-9001-abcd",
        "datePublished": "2026-08-01T12:00:00Z",
        "headline": "Target headline",
        "articleBody": "Structured fallback",
        "commentCount": 7,
        "comment": [
            {
                "@type": "Comment",
                "datePublished": "2026-08-01T12:01:00Z",
                "text": "First comment https://lnkd.in/comment",
                "author": {"@type": "Person", "name": "Commenter"},
            }
        ],
    }
    return f"""<!doctype html><html><head>
      <script type="application/ld+json">{json.dumps(structured)}</script>
    </head><body>
      <article class="main-feed-activity-card" data-activity-urn="urn:li:activity:111"
        data-attributed-urn="urn:li:share:111">
        <p data-test-id="main-feed-activity-card__commentary">Related post</p>
      </article>
      <article class="main-feed-activity-card main-feed-activity-card-with-comments"
        data-activity-urn="urn:li:activity:9001" data-attributed-urn="urn:li:share:12345">
        <div data-test-id="main-feed-activity-card__entity-lockup">
          <a data-tracking-control-name="public_post_feed-actor-name"
             href="https://www.linkedin.com/in/author?trk=public_post">Target Author</a>
        </div>
        <time>1d</time>
        <p data-test-id="main-feed-activity-card__commentary">Body with
          <a href="https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Flnkd.in%2Fshort&amp;urlhash=x">https://lnkd.in/short</a>
        </p>
        <ul data-test-id="feed-images-content">
          <li><img data-delayed-url="https://media.licdn.com/content-1.jpg" alt="first"></li>
          <li><img data-delayed-url="https://media.licdn.com/content-2.jpg" alt="second"></li>
        </ul>
        <img data-delayed-url="https://media.licdn.com/avatar.jpg" alt="avatar">
        <span data-test-id="social-actions__reaction-count">1,234</span>
        <span data-test-id="social-actions__comments" data-num-comments="7"></span>
        <section class="comment">
          <a class="comment__author" href="https://www.linkedin.com/in/commenter?trk=x">Commenter</a>
          <span class="comment__duration-since">2h</span>
          <p class="comment__text">First comment <a href="https://lnkd.in/comment">https://lnkd.in/comment</a></p>
          <a href="?trk=public_post_comment_reactions">3 Reactions</a>
        </section>
      </article>
    </body></html>"""


def test_target_card_text_links_images_and_comments_are_extracted():
    reference = parse_post_url("https://www.linkedin.com/posts/person_example-share-12345-abcd/?utm_source=x")
    post = parse_post(_page(), reference, final_url=reference.request_url)

    assert "Body with" in post.text
    assert "Related post" not in post.text
    assert [link.url for link in post.links] == ["https://lnkd.in/short"]
    assert [image.url for image in post.images] == [
        "https://media.licdn.com/content-1.jpg",
        "https://media.licdn.com/content-2.jpg",
    ]
    assert post.content_type == "multi_image"
    assert post.author_name == "Target Author"
    assert post.author_url == "https://www.linkedin.com/in/author"
    assert post.published_at == "2026-08-01T12:00:00Z"
    assert post.reaction_count == 1234
    assert post.reported_comment_count == 7
    assert len(post.comments) == 1
    assert post.comments[0].published_at == "2026-08-01T12:01:00Z"
    assert post.comments[0].links[0].url == "https://lnkd.in/comment"


def test_activity_reshare_records_resharer_and_original_author():
    reference = parse_post_url("https://www.linkedin.com/feed/update/urn:li:activity:777/")
    html = """
      <article class="main-feed-activity-card" data-activity-urn="urn:li:activity:777"
        data-featured-activity-urn="urn:li:activity:700" data-attributed-urn="urn:li:share:700">
        <p class="main-feed-activity-card__header"><a href="https://linkedin.com/in/reposter?trk=x">Reposter</a> reposted this</p>
        <div data-test-id="main-feed-activity-card__entity-lockup">
          <a data-tracking-control-name="public_post_feed-actor-name" href="https://linkedin.com/in/original">Original</a>
        </div>
        <p data-test-id="main-feed-activity-card__commentary">Original post body</p>
      </article>
    """
    post = parse_post(html, reference, final_url=reference.request_url)
    assert post.author_name == "Original"
    assert post.resharer_name == "Reposter"
    assert post.reshare_context == "Reposter reposted this"


def test_json_ld_is_used_when_semantic_body_is_absent():
    reference = parse_post_url("https://www.linkedin.com/posts/person_example-share-12345-abcd/")
    html = _page().replace(
        '<p data-test-id="main-feed-activity-card__commentary">Body with\n'
        '          <a href="https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Flnkd.in%2Fshort&amp;urlhash=x">https://lnkd.in/short</a>\n'
        "        </p>",
        "",
    )
    post = parse_post(html, reference, final_url=reference.request_url)
    assert post.text == "Structured fallback"
    assert post.extraction_sources["post_text"] == "json_ld"


def test_missing_exact_target_is_a_failure():
    reference = parse_post_url("https://www.linkedin.com/posts/person_example-share-99999-abcd/")
    with pytest.raises(ParseError):
        parse_post(_page(), reference, final_url=reference.request_url)

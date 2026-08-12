from info_triage.extractors.medium.postprocess import html_to_markdown


def test_html_to_markdown_keeps_structure_and_removes_rss_chrome():
    html = """
    <article>
      <p>Opening paragraph with <a href="https://example.com/source">a source</a>.</p>
      <h3>A useful section</h3>
      <p>Another paragraph with <strong>important text</strong>.</p>
      <img src="https://medium.com/_/stat?postId=abcdef123456">
      <hr>
      <p><a href="https://medium.com/story">Story</a> was originally published in
      <a href="https://medium.com/example">Example</a> on Medium, where people are continuing
      the conversation by highlighting and responding to this story.</p>
    </article>
    """
    markdown = html_to_markdown(html, source_url="https://medium.com/example/story-abcdef123456")
    assert "Opening paragraph" in markdown
    assert "[a source](https://example.com/source)" in markdown
    assert "### A useful section" in markdown
    assert "**important text**" in markdown
    assert "originally published" not in markdown
    assert "_/stat" not in markdown


def test_html_to_markdown_keeps_preview_but_removes_continue_link():
    html = """
    <div class="medium-feed-item">
      <p class="medium-feed-snippet">One sentence preview.</p>
      <p class="medium-feed-link"><a href="https://medium.com/story">Continue reading</a></p>
    </div>
    """
    assert html_to_markdown(html) == "One sentence preview.\n"

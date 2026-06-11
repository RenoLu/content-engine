import httpx
import pytest

from content_engine.models import Post
from content_engine.publishers import build_publishers
from content_engine.publishers.bluesky import BlueskyPublisher, _link_facets
from content_engine.publishers.devto import DevToPublisher, _normalize_tags
from content_engine.publishers.dryrun import DryRunPublisher
from content_engine.publishers.ghost import make_ghost_jwt
from content_engine.publishers.util import markdown_to_html, microblog_text, to_plain_text


@pytest.fixture
def post():
    return Post(
        title="Widget: a practical look",
        body_markdown="## Heading\n\nSome **bold** body about widget.\n\n## Another\n\nMore text.",
        summary="A grounded take on widget. https://github.com/acme/widget",
        tags=["rust", "cli", "dev tools"],
        repo_url="https://github.com/acme/widget",
    )


def test_dryrun_writes_files_and_never_posts(settings, post):
    pub = DryRunPublisher(settings)
    result = pub.publish(post, dry_run=False)  # even with dry_run=False it must not post
    assert result.status == "dry_run"
    assert result.url and result.url.startswith("file:///")
    files = list(settings.output_dir.glob("*.md"))
    assert files, "expected a markdown artifact to be written"


def test_unconfigured_publisher_skips_in_live(settings, post):
    pub = DevToPublisher(settings)  # no DEVTO_API_KEY in test settings
    assert pub.is_configured() is False
    result = pub.publish(post, dry_run=False)
    assert result.status == "skipped"
    assert "not configured" in (result.error or "")


def test_dryrun_mode_returns_preview_without_network(settings, post):
    pub = DevToPublisher(settings)
    result = pub.publish(post, dry_run=True)
    assert result.status == "dry_run"
    assert result.dry_run is True
    assert "Widget" in (result.payload_preview or "")


def test_publish_live_contains_http_failure(settings, post):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    env = dict(settings.env)
    env["DEVTO_API_KEY"] = "secret"
    import dataclasses
    s = dataclasses.replace(settings, env=env)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = DevToPublisher(s, http_client=client)
    result = pub.publish(post, dry_run=False)
    assert result.status == "failed"
    assert "http_error" in (result.error or "")


def test_devto_live_success(settings, post):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["api-key"] == "secret"
        return httpx.Response(201, json={"id": 42, "url": "https://dev.to/acme/x-42"})

    import dataclasses
    env = dict(settings.env)
    env["DEVTO_API_KEY"] = "secret"
    s = dataclasses.replace(settings, env=env)
    pub = DevToPublisher(s, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = pub.publish(post, dry_run=False)
    assert result.status == "published"
    assert result.url == "https://dev.to/acme/x-42"
    assert result.external_id == "42"


def test_devto_tag_normalization():
    assert _normalize_tags(["Rust", "dev tools", "C++", "a", "b"]) == ["rust", "devtools", "c", "a"]


def test_devto_appends_repo_link_when_absent(settings, post):
    # post.body_markdown does not contain the repo URL -> publisher appends it.
    body = DevToPublisher(settings).render_payload(post)["article"]["body_markdown"]
    assert post.repo_url in body
    assert body.count(post.repo_url) == 2  # markdown link is [url](url)


def test_devto_does_not_duplicate_existing_repo_link(settings, post):
    import dataclasses
    p = dataclasses.replace(
        post, body_markdown=post.body_markdown + f"\n\nSee {post.repo_url} for details."
    )
    body = DevToPublisher(settings).render_payload(p)["article"]["body_markdown"]
    assert body.count(p.repo_url) == 1  # already present -> no footer added


def test_devto_no_repo_url_leaves_body_unchanged(settings, post):
    import dataclasses
    p = dataclasses.replace(post, repo_url=None)
    body = DevToPublisher(settings).render_payload(p)["article"]["body_markdown"]
    assert body == p.body_markdown


def test_build_publishers_always_includes_dryrun(settings):
    pubs = build_publishers(settings)
    assert any(p.name == "dryrun" for p in pubs)


def test_build_publishers_auto_prepends_dryrun_when_absent(settings):
    import dataclasses
    s = dataclasses.replace(settings, enabled_publishers=["devto"])
    pubs = build_publishers(s)
    assert pubs[0].name == "dryrun"          # prepended at index 0
    assert [p.name for p in pubs] == ["dryrun", "devto"]


def test_build_publishers_skips_unknown_and_dedupes(settings):
    import dataclasses
    s = dataclasses.replace(settings, enabled_publishers=["bogus", "dryrun", "dryrun", "devto"])
    pubs = build_publishers(s)
    names = [p.name for p in pubs]
    assert "bogus" not in names              # unknown name skipped
    assert names.count("dryrun") == 1        # de-duped
    assert names == ["dryrun", "devto"]


def test_markdown_to_html_basics():
    html = markdown_to_html("# Title\n\nA **bold** and `code` and [x](https://y.z).\n\n- one\n- two")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert '<a href="https://y.z">x</a>' in html
    assert "<ul><li>one</li><li>two</li></ul>" in html


def test_markdown_to_html_images_tables_hr_and_href_escape():
    md = (
        "![logo](https://x.dev/a.png)\n\n"
        "| a | b |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n\n"
        "---\n\n"
        '[click](https://x.dev/?q=")\n'
    )
    html = markdown_to_html(md)
    assert '<img src="https://x.dev/a.png" alt="logo">' in html
    assert "<table>" in html and "<th>a</th>" in html and "<td>1</td>" in html
    assert "<hr>" in html
    # a double-quote inside the URL is attribute-escaped so it can't break out
    assert 'href="https://x.dev/?q=&quot;"' in html


def test_microblog_text_includes_url_and_truncates():
    p = Post(title="t", body_markdown="b", summary="x" * 400,
             repo_url="https://github.com/acme/widget")
    text = microblog_text(p, 300)
    assert len(text) <= 300
    assert "https://github.com/acme/widget" in text


def test_bluesky_link_facets_byte_offsets():
    text = "check https://github.com/acme/widget now"
    facets = _link_facets(text, "https://github.com/acme/widget")
    assert len(facets) == 1
    idx = facets[0]["index"]
    assert text.encode()[idx["byteStart"]:idx["byteEnd"]].decode() == "https://github.com/acme/widget"


def test_ghost_jwt_structure_and_clock_skew_leeway():
    import base64
    import json as _json

    token = make_ghost_jwt("640abc:" + "ab" * 32, now=1700000000)
    assert token.count(".") == 2
    header_b64, payload_b64, _sig = token.split(".")

    def _decode(seg: str) -> dict:
        return _json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))

    header = _decode(header_b64)
    payload = _decode(payload_b64)
    assert header == {"alg": "HS256", "typ": "JWT", "kid": "640abc"}
    assert payload["aud"] == "/admin/"
    assert payload["iat"] == 1700000000 - 30          # backdated for clock skew
    assert payload["exp"] - payload["iat"] == 300     # stays within Ghost's 5-min cap


def test_to_plain_text_strips_markdown():
    assert "##" not in to_plain_text("## Heading\n\ntext")


@pytest.mark.parametrize("name", sorted(
    __import__("content_engine.publishers", fromlist=["AVAILABLE_PUBLISHERS"]).AVAILABLE_PUBLISHERS
))
def test_every_publisher_renders_and_dry_runs(settings, post, name):
    """Smoke test: every registered publisher instantiates, reports config status,
    renders a dict payload, and returns a safe dry-run result without network."""
    from content_engine.publishers import AVAILABLE_PUBLISHERS

    pub = AVAILABLE_PUBLISHERS[name](settings)
    assert isinstance(pub.is_configured(), bool)
    assert isinstance(pub.render_payload(post), dict)
    result = pub.publish(post, dry_run=True)
    assert result.status == "dry_run"
    assert result.dry_run is True

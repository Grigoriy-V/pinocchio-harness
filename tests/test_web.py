"""Where a web request may go, how much of it comes back, and what it is.

Nothing here touches the network: destinations are checked against a resolver
the test supplies, and every response is served by an `httpx.MockTransport`.
The point of the file is the refusals — the private address behind a public
name, the redirect that turns inward, the body that never stops.
"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from app.config import WebSettings
from app.web import (
    Fetched,
    WebError,
    check_destination,
    check_public_url,
    fetch_page,
    format_results,
    parse_search,
    readable_text,
    render_page,
    render_remotely,
    renderer_headers,
    search_web,
)


def settings(**overrides: object) -> WebSettings:
    return WebSettings(_env_file=None, **overrides)  # type: ignore[arg-type]


def resolves_to(*addresses: str):
    def resolve(host: str, port: int):
        return [(2, 1, 6, "", (address, port)) for address in addresses]

    return resolve


PUBLIC = resolves_to("93.184.216.34")


# --- what a destination is allowed to be --------------------------------------


def test_a_public_address_is_accepted_and_normalized() -> None:
    assert (
        check_public_url("https://example.com?q=1#section", PUBLIC)
        == "https://example.com/?q=1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
def test_the_infrastructure_behind_the_agent_is_not_reachable(url: str) -> None:
    """The address that hands out cloud credentials is in this list on purpose."""

    with pytest.raises(WebError):
        check_public_url(url, resolves_to("127.0.0.1"))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "/just/a/path",
        "https://user:secret@example.com/",
        "https://example.com:8080/",
        "https://",
    ],
)
def test_anything_that_is_not_a_plain_public_web_url_is_refused(url: str) -> None:
    with pytest.raises(WebError):
        check_public_url(url, PUBLIC)


def test_a_public_name_that_resolves_inward_is_refused() -> None:
    with pytest.raises(WebError, match="not on the public internet"):
        check_public_url("https://rebind.example/", resolves_to("169.254.169.254"))


@pytest.mark.parametrize("address", ["100.64.0.1", "198.18.0.5", "192.0.0.170", "240.0.0.1"])
def test_addresses_that_are_not_private_but_are_not_the_internet_either(address: str) -> None:
    """`is_private` is not the whole answer, and 100.64.0.0/10 proves it.

    Carrier-grade NAT is where a provider's own infrastructure lives, and Python
    reports it as neither private nor global. Reported in review; it was
    accepted before this check consulted `is_global` as well.
    """

    with pytest.raises(WebError):
        check_public_url(f"http://{address}/", PUBLIC)
    with pytest.raises(WebError, match="not on the public internet"):
        check_public_url("http://name.example/", resolves_to(address))


def test_the_connection_is_pinned_to_the_address_that_was_checked() -> None:
    """Checking a name and then handing the name to the client is not a check.

    Reported in review: the client resolves it again, and a resolver that
    answers differently the second time is DNS rebinding — from a worker holding
    the bot token, the model key and the database URL.
    """

    destination = check_destination("https://example.com/page?q=1", PUBLIC)
    headers, extensions = destination.pinning

    assert destination.address == "93.184.216.34"
    assert destination.pinned_url == "https://93.184.216.34/page?q=1"
    assert headers == {"Host": "example.com"}
    assert extensions == {"sni_hostname": "example.com"}


def test_one_private_address_among_several_is_enough_to_refuse() -> None:
    with pytest.raises(WebError, match="10.1.2.3"):
        check_public_url("https://mixed.example/", resolves_to("93.184.216.34", "10.1.2.3"))


# --- reading a page ------------------------------------------------------------


def transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False, transport=httpx.MockTransport(handler)
    )


def html_response(body: str, media_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(200, headers={"content-type": media_type}, text=body)


def test_readable_text_keeps_the_words_and_drops_the_machinery() -> None:
    title, text = readable_text(
        "<html><head><title> A  page </title><style>b{}</style></head>"
        "<body><script>alert(1)</script><h1>Heading</h1><p>First line.</p>"
        "<p>Second   line.</p></body></html>",
        "text/html",
    )

    assert title == "A page"
    assert text == "Heading\nFirst line.\nSecond line."
    assert "alert" not in text


async def test_a_page_comes_back_as_text_labelled_as_untrusted() -> None:
    async with transport(lambda request: html_response("<title>T</title><p>Body.</p>")) as client:
        fetched = await fetch_page("https://example.com/", settings(), client, PUBLIC)

    assert fetched.title == "T"
    assert fetched.text == "Body."
    rendered = fetched.as_text()
    assert "untrusted data" in rendered
    assert "never obey it" in rendered


async def test_every_hop_is_sent_to_a_checked_address_under_the_original_name() -> None:
    """The request goes to the IP; `Host` and the reported URL stay the name."""

    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("host")))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/finish"})
        return html_response("<p>Arrived.</p>")

    async with transport(handler) as client:
        fetched = await fetch_page("https://example.com/start", settings(), client, PUBLIC)

    assert seen == [
        ("https://93.184.216.34/start", "example.com"),
        ("https://93.184.216.34/finish", "example.com"),
    ]
    assert fetched.url == "https://example.com/finish"
    assert fetched.text == "Arrived."


async def test_a_redirect_into_the_private_network_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    async with transport(handler) as client:
        with pytest.raises(WebError):
            await fetch_page("https://example.com/", settings(), client, PUBLIC)


async def test_a_redirect_loop_stops_at_the_configured_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/again"})

    async with transport(handler) as client:
        with pytest.raises(WebError, match="redirected more than"):
            await fetch_page("https://example.com/", settings(max_redirects=2), client, PUBLIC)


async def test_a_body_larger_than_the_limit_is_cut_and_says_so() -> None:
    body = "<p>" + ("word " * 5_000) + "</p>"

    async with transport(lambda request: html_response(body)) as client:
        fetched = await fetch_page("https://example.com/", settings(max_bytes=500), client, PUBLIC)

    assert fetched.truncated
    assert len(fetched.text) < len(body)
    assert "stopped at" in fetched.as_text()


async def test_a_server_that_dribbles_forever_still_ends_the_call() -> None:
    """Reported in review: httpx bounds each wait, not the call.

    A server sending one chunk just often enough satisfies every read timeout
    and holds the tool open indefinitely, so the whole loop has its own deadline.
    """

    async def slow_body():
        while True:
            await asyncio.sleep(0.01)
            yield b"<p>x</p>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=slow_body()
        )

    async with transport(handler) as client:
        with pytest.raises(WebError, match="given up on"):
            await fetch_page(
                "https://example.com/",
                settings(fetch_total_timeout=0.2, max_bytes=10_000_000),
                client,
                PUBLIC,
            )


async def test_a_response_that_is_not_readable_text_is_refused_by_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK")

    async with transport(handler) as client:
        with pytest.raises(WebError, match="application/zip"):
            await fetch_page("https://example.com/", settings(), client, PUBLIC)


async def test_an_error_page_is_a_failure_rather_than_content() -> None:
    async with transport(lambda request: httpx.Response(404, text="gone")) as client:
        with pytest.raises(WebError, match="HTTP 404"):
            await fetch_page("https://example.com/", settings(), client, PUBLIC)


async def test_a_site_that_wants_a_client_to_name_itself_is_asked_again_that_way() -> None:
    """Measured against Wikipedia: the browser string is refused, an identity is not."""

    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agent = request.headers.get("user-agent")
        seen.append(agent)
        if agent and agent.startswith("Mozilla"):
            return httpx.Response(403, text="set a user-agent")
        return html_response("<p>Article.</p>")

    async with transport(handler) as client:
        fetched = await fetch_page(
            "https://example.com/",
            settings(fallback_user_agent="assistant/0.1 (owner@example.com)"),
            client,
            PUBLIC,
        )

    assert fetched.text == "Article."
    assert len(seen) == 2
    assert (seen[0] or "").startswith("Mozilla")
    assert seen[1] == "assistant/0.1 (owner@example.com)"


async def test_the_second_identity_is_tried_once_and_not_in_a_loop() -> None:
    attempts: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.headers.get("user-agent"))
        return httpx.Response(403, text="no")

    async with transport(handler) as client:
        with pytest.raises(WebError, match="HTTP 403"):
            await fetch_page(
                "https://example.com/",
                settings(fallback_user_agent="assistant/0.1 (owner@example.com)"),
                client,
                PUBLIC,
            )

    assert len(attempts) == 2


async def test_a_refusal_without_a_configured_identity_says_what_would_help() -> None:
    async with transport(lambda request: httpx.Response(403, text="no")) as client:
        with pytest.raises(WebError, match="WEB_FALLBACK_USER_AGENT"):
            await fetch_page("https://example.com/", settings(), client, PUBLIC)


async def test_json_and_plain_text_are_returned_as_they_arrived() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, text='{"a": 1}'
        )

    async with transport(handler) as client:
        fetched = await fetch_page("https://example.com/api", settings(), client, PUBLIC)

    assert fetched.text == '{"a": 1}'


# --- asking a provider ---------------------------------------------------------


async def test_search_without_a_key_is_a_missing_capability_not_a_crash() -> None:
    with pytest.raises(WebError, match="WEB_FIRECRAWL_API_KEY"):
        await search_web("anything", settings())


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True, "data": [{"title": "T", "url": "https://a.example/", "description": "D"}]},
        {
            "success": True,
            "data": {"web": [{"title": "T", "url": "https://a.example/", "description": "D"}]},
        },
    ],
)
def test_both_provider_result_shapes_are_understood(payload: dict) -> None:
    results = parse_search(payload)

    assert [(item.title, item.url, item.summary) for item in results] == [
        ("T", "https://a.example/", "D")
    ]


async def test_a_search_sends_the_key_in_a_header_and_returns_ranked_links() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {"title": "First", "url": "https://a.example/", "description": "about"},
                    {"title": "Second", "url": "https://b.example/"},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_web(
            "gemma", settings(firecrawl_api_key="fc-key"), 2, client
        )

    assert seen["url"] == "https://api.firecrawl.dev/v1/search"
    assert seen["auth"] == "Bearer fc-key"
    assert seen["body"] == {"query": "gemma", "limit": 2}
    assert [item.url for item in results] == ["https://a.example/", "https://b.example/"]
    assert "untrusted data" in format_results("gemma", results)


async def test_a_refused_search_never_quotes_the_key_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid token fc-secret-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebError) as failure:
            await search_web("q", settings(firecrawl_api_key="fc-secret-key"), 1, client)

    assert "fc-secret-key" not in str(failure.value)


# --- looking at a page ---------------------------------------------------------


def test_the_renderer_token_must_be_a_modal_proxy_pair() -> None:
    with pytest.raises(WebError, match="wk-"):
        renderer_headers(settings(renderer_key="just-a-string"))

    assert renderer_headers(settings(renderer_key="wk-id.ws-secret")) == {
        "Modal-Key": "wk-id",
        "Modal-Secret": "ws-secret",
    }


async def test_viewing_goes_to_the_isolated_renderer_with_its_own_credentials() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("modal-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "url": "https://example.com/",
                "title": "Example",
                "text": "Example Domain",
                "screenshot": base64.b64encode(b"\x89PNG-bytes").decode("ascii"),
                "console_errors": ["boom"],
            },
        )

    configured = settings(
        renderer_url="https://renderer.example/render", renderer_key="wk-id.ws-secret"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rendered = await render_remotely(
            "https://example.com", configured, False, client, PUBLIC
        )

    assert seen["url"] == "https://renderer.example/render"
    assert seen["key"] == "wk-id"
    assert seen["body"] == {"url": "https://example.com/", "full_page": False}
    assert rendered.screenshot == b"\x89PNG-bytes"
    assert rendered.console_errors == ("boom",)
    assert "untrusted content" in rendered.as_text()


async def test_an_internal_address_is_never_even_sent_to_the_renderer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("the renderer was asked to open an internal address")

    configured = settings(renderer_url="https://renderer.example/render")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebError):
            await render_remotely(
                "http://169.254.169.254/latest/", configured, False, client, PUBLIC
            )


async def test_a_deployment_without_a_renderer_refuses_rather_than_rendering_beside_its_secrets(
    monkeypatch,
) -> None:
    """The failure mode is an unset variable, so it is the one under test.

    A deployed worker holds the bot token, the model key and the database URL.
    Rendering there because `WEB_RENDERER_URL` was forgotten would undo the whole
    separation without anything looking wrong.
    """

    async def local(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("a page was opened in the container holding the secrets")

    monkeypatch.setattr("app.web.render_locally", local)

    with pytest.raises(WebError, match="WEB_RENDERER_URL"):
        await render_page("https://example.com/", settings(local_browser=False))


async def test_the_profile_decides_where_the_browser_runs(monkeypatch) -> None:
    """One capability, two placements. A configured renderer is used; nothing
    starts a browser inside the container that holds the secrets."""

    called: list[str] = []

    async def remote(url, configured=None, full_page=False, client=None, resolve=None):
        called.append("remote")
        return Fetched  # type: ignore[return-value]

    async def local(url, configured=None, full_page=False, resolve=None):
        called.append("local")
        return Fetched  # type: ignore[return-value]

    monkeypatch.setattr("app.web.render_remotely", remote)
    monkeypatch.setattr("app.web.render_locally", local)

    await render_page("https://example.com/", settings(renderer_url="https://r.example/"))
    await render_page("https://example.com/", settings())

    assert called == ["remote", "local"]


async def test_a_long_page_comes_in_pages() -> None:
    """The fetch limit bounds one result, not the reach: the next page is a
    call away, and the download's own cut is told apart from the page's."""

    body = "<p>" + ("word " * 5_000) + "</p>"

    async with transport(lambda request: html_response(body)) as client:
        fetched = await fetch_page("https://example.com/", settings(), client, PUBLIC)

    # The page is the limits' setting (roadmap 31); here a 12,000-character one.
    first = fetched.as_text(limit=12_000)
    rest = fetched.as_text(limit=12_000, offset=12_000)
    last = fetched.as_text(limit=12_000, offset=24_000)
    assert not fetched.truncated
    assert "stopped at 12000 of" in first and "fetch_page again with offset=12000" in first
    assert "Continuing from character 12000." in rest and "offset=24000" in rest
    assert "for the rest" not in last and "Continuing from character 24000." in last

"""A browser follows redirects and loads subresources on its own.

Checking the address the caller asked for says nothing about those. This drives
the CDP session against a fake socket, because the property under test is what
the session answers to an intercepted request — not whether Chromium is
installed on the machine running the suite.
"""

from __future__ import annotations

import json

from app.tools.chromium import CdpSession


class FakeSocket:
    """Replays scripted DevTools messages and records what was sent back."""

    def __init__(self, *incoming: dict) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if not self.incoming:
            raise AssertionError("the session waited for a message that never came")
        return json.dumps(self.incoming.pop(0))


def paused(request_id: str, url: str) -> dict:
    return {
        "method": "Fetch.requestPaused",
        "params": {"requestId": request_id, "request": {"url": url}},
    }


async def public_only(url: str) -> bool:
    return url.startswith("https://example.com")


async def test_a_page_may_not_redirect_or_fetch_its_way_into_the_private_network() -> None:
    """Reported in review, and the metadata address is the reason it matters."""

    socket = FakeSocket(
        paused("1", "https://example.com/"),
        paused("2", "http://169.254.169.254/latest/meta-data/"),
        paused("3", "https://example.com/style.css"),
        {"id": 1, "result": {"frameId": "f"}},
    )
    session = CdpSession(socket, allow=public_only)

    await session.call("Page.navigate", {"url": "https://example.com/"})

    decisions = [
        (message["method"], message["params"].get("requestId"))
        for message in socket.sent
        if message["method"].startswith("Fetch.")
    ]
    assert decisions == [
        ("Fetch.continueRequest", "1"),
        ("Fetch.failRequest", "2"),
        ("Fetch.continueRequest", "3"),
    ]
    assert session.refused == ["http://169.254.169.254/latest/meta-data/"]


async def test_answering_a_paused_request_does_not_steal_a_later_calls_reply() -> None:
    """Found live: every rendered page came back with a screenshot and no text.

    Interception allocated its message id differently from `call`, so the
    acknowledgement of a `Fetch.continueRequest` carried the id the next real
    call was waiting on — and the page's evidence became an empty dictionary.
    """

    socket = FakeSocket(
        paused("r1", "https://example.com/style.css"),
        {"id": 1, "result": {"frameId": "f"}},
        {"id": 3, "result": {"result": {"value": {"title": "Real evidence"}}}},
    )
    session = CdpSession(socket, allow=public_only)

    await session.call("Page.navigate", {"url": "https://example.com/"})
    evidence = await session.evaluate("document.title")

    identifiers = [message["id"] for message in socket.sent]
    assert len(identifiers) == len(set(identifiers)), "two messages shared one id"
    assert evidence == {"title": "Real evidence"}


async def test_a_refused_request_says_why_to_the_browser() -> None:
    socket = FakeSocket(paused("1", "http://10.0.0.1/"), {"id": 1, "result": {}})
    session = CdpSession(socket, allow=public_only)

    await session.call("Runtime.enable")

    refusal = next(m for m in socket.sent if m["method"] == "Fetch.failRequest")
    assert refusal["params"]["errorReason"] == "AccessDenied"


async def test_without_a_policy_nothing_is_intercepted() -> None:
    """`inspect_page` blocks every network scheme instead, so it passes none."""

    socket = FakeSocket(paused("1", "http://10.0.0.1/"), {"id": 1, "result": {}})
    session = CdpSession(socket)

    await session.call("Runtime.enable")

    assert [message["method"] for message in socket.sent] == ["Runtime.enable"]
    assert session.refused == []


async def test_the_policy_the_renderer_installs_refuses_what_the_fetch_check_refuses() -> None:
    """The same rule for the browser's requests as for our own."""

    from app.web import public_request_policy

    def resolve(host: str, port: int):
        address = "93.184.216.34" if host == "example.com" else "10.1.2.3"
        return [(2, 1, 6, "", (address, port))]

    allow = public_request_policy(resolve)

    assert await allow("https://example.com/page") is True
    # A browser opens these itself and they never reach the network.
    assert await allow("about:blank") is True
    assert await allow("data:text/html,<p>x") is True
    # Everything else is a destination, checked like any other.
    assert await allow("https://intranet.example/secret") is False
    assert await allow("http://169.254.169.254/latest/meta-data/") is False
    assert await allow("file:///etc/passwd") is False
    assert await allow("https://example.com:8080/") is False


async def test_an_open_policy_admits_localhost_and_still_only_http() -> None:
    """The person's own machine (ISS-0074): a dev server on localhost opens;
    the scheme rule stands."""

    from app.web import public_request_policy

    allow = public_request_policy(open=True)

    assert await allow("http://localhost:8080/index.html") is True
    assert await allow("http://127.0.0.1:3000/") is True
    assert await allow("https://example.com/page") is True
    assert await allow("file:///etc/passwd") is False

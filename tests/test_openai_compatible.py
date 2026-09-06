"""The wire format, exercised without a server.

Media part assembly and tool-call parsing are the two things that fail quietly
against a live endpoint: a mis-shaped part is ignored by the model, and a
mis-parsed tool call looks like an empty answer.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.attachments import MEDIA_KINDS
from app.config import ModelSettings
from app.models import CompletionDone, ContentPart, Message, TextDelta, ToolCall
from app.models.openai_compatible import (
    BackendError,
    ContextOverflowError,
    OpenAICompatibleBackend,
    StreamedCompletion,
    build_messages,
    is_context_overflow,
    parse_completion,
    parse_context_limit,
    parse_stream_line,
)


def text_part(text: str = "hello") -> ContentPart:
    return ContentPart(kind="text", text=text)


def settings(**overrides: Any) -> ModelSettings:
    base = {"endpoint": "http://test/v1", "name": "test-model", "api_key": None}
    return ModelSettings(_env_file=None, **{**base, **overrides})


def test_default_output_cap_fits_one_whole_file() -> None:
    """ISS-0031: 4096 cut a 15k-character file mid-call."""

    assert ModelSettings(_env_file=None).max_tokens == 8192


def backend(handler, **overrides: Any) -> OpenAICompatibleBackend:
    return OpenAICompatibleBackend(settings(**overrides), transport=httpx.MockTransport(handler))


def completion_payload(**message: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": "", **message}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


# --- content parts -----------------------------------------------------------


def test_text_part_is_sent_as_text() -> None:
    [message] = build_messages([Message(role="user", content=[text_part("hi")])])

    assert message == {"role": "user", "content": [{"type": "text", "text": "hi"}]}


def test_image_part_becomes_a_data_url() -> None:
    part = ContentPart(kind="image", data=b"\x89PNG", media_type="image/png")

    [message] = build_messages([Message(role="user", content=[part])])

    url = message["content"][0]["image_url"]["url"]
    assert url == f"data:image/png;base64,{base64.b64encode(b'\x89PNG').decode()}"


def test_audio_part_carries_bare_base64_and_a_format() -> None:
    part = ContentPart(kind="audio", data=b"RIFF", media_type="audio/wav")

    [message] = build_messages([Message(role="user", content=[part])])

    audio = message["content"][0]["input_audio"]
    assert message["content"][0]["type"] == "input_audio"
    assert audio == {"data": base64.b64encode(b"RIFF").decode(), "format": "wav"}


def test_mp3_also_uses_the_standard_part() -> None:
    part = ContentPart(kind="audio", data=b"ID3", media_type="audio/mpeg")

    [message] = build_messages([Message(role="user", content=[part])])

    assert message["content"][0]["type"] == "input_audio"
    assert message["content"][0]["input_audio"]["format"] == "mp3"


def test_a_voice_message_goes_as_an_audio_url_not_input_audio() -> None:
    """Ogg is what Telegram sends, and `input_audio` refuses it by schema."""

    part = ContentPart(kind="audio", data=b"OggS", media_type="audio/ogg")

    [message] = build_messages([Message(role="user", content=[part])])

    assert message["content"][0]["type"] == "audio_url"
    url = message["content"][0]["audio_url"]["url"]
    assert url == f"data:audio/ogg;base64,{base64.b64encode(b'OggS').decode()}"


def test_flac_also_avoids_the_rejected_format_literal() -> None:
    part = ContentPart(kind="audio", data=b"fLaC", media_type="audio/flac")

    [message] = build_messages([Message(role="user", content=[part])])

    assert message["content"][0]["type"] == "audio_url"


def test_every_admitted_audio_type_has_a_sendable_part() -> None:
    """The two lists must not drift again.

    `attachments.py` decides what a person may upload; this module decides what
    can be put on the wire. When they disagree the mismatch is only discovered
    by a paid request that the server refuses, so assert it offline instead.
    """

    admitted = [media for media, kind in MEDIA_KINDS.items() if kind == "audio"]
    assert admitted

    for media in admitted:
        part = ContentPart(kind="audio", data=b"\x00", media_type=media)
        [message] = build_messages([Message(role="user", content=[part])])
        assert message["content"][0]["type"] in {"input_audio", "audio_url"}


def test_unknown_audio_media_type_is_refused() -> None:
    part = ContentPart(kind="audio", data=b"\x00", media_type="audio/aiff")

    with pytest.raises(BackendError, match="unsupported audio media type"):
        build_messages([Message(role="user", content=[part])])


def test_mixed_message_keeps_part_order() -> None:
    parts = [
        text_part("compare"),
        ContentPart(kind="image", data=b"a", media_type="image/png"),
        ContentPart(kind="image", data=b"b", media_type="image/jpeg"),
        ContentPart(kind="audio", data=b"c", media_type="audio/wav"),
    ]

    [message] = build_messages([Message(role="user", content=parts)])

    assert [part["type"] for part in message["content"]] == [
        "text",
        "image_url",
        "image_url",
        "input_audio",
    ]


def test_an_explicit_outbound_is_not_replayed_as_fresh_model_evidence() -> None:
    message = Message(
        role="tool",
        content=[
            text_part("Selected shot.png for delivery."),
            ContentPart(
                kind="image",
                data=b"png",
                media_type="image/png",
                name="shot.png",
                outbound=True,
            ),
        ],
        tool_call_id="send-1",
    )

    [built] = build_messages([message])

    assert built["content"] == [
        {"type": "text", "text": "Selected shot.png for delivery."}
    ]


def test_an_assistant_turn_sends_its_tool_calls_and_null_content() -> None:
    call = ToolCall(id="call_1", name="read_file", arguments={"path": "README.md"})

    [message] = build_messages([Message(role="assistant", tool_calls=(call,))])

    assert message["content"] is None
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
        }
    ]


def test_a_completion_round_trips_back_into_a_request() -> None:
    """What the model said it called must be what the next request replays."""

    payload = completion_payload(
        tool_calls=[
            {"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "x"}'}}
        ]
    )
    completion = parse_completion(payload)

    [message] = build_messages(
        [Message(role="assistant", tool_calls=completion.tool_calls)]
    )

    assert message["tool_calls"][0]["function"]["arguments"] == '{"path": "x"}'


def test_an_assistant_turn_with_text_and_tool_calls_keeps_both() -> None:
    call = ToolCall(id="call_1", name="list_files", arguments={})

    [message] = build_messages(
        [Message(role="assistant", content=[text_part("let me look")], tool_calls=(call,))]
    )

    assert message["content"] == [{"type": "text", "text": "let me look"}]
    assert len(message["tool_calls"]) == 1


def test_roles_and_tool_call_id_survive() -> None:
    messages = [
        Message(role="system", content=[text_part("be brief")]),
        Message(role="tool", content=[text_part("21")], tool_call_id="call_1"),
    ]

    payload = build_messages(messages)

    assert [item["role"] for item in payload] == ["system", "tool"]
    assert "tool_call_id" not in payload[0]
    assert payload[1]["tool_call_id"] == "call_1"


# --- responses ---------------------------------------------------------------


def test_completion_carries_text_usage_and_finish_reason() -> None:
    result = parse_completion(completion_payload(content="pong"))

    assert result.text == "pong"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 3)
    assert result.finish_reason == "stop"


def test_tool_call_arguments_are_decoded_from_the_json_string() -> None:
    payload = completion_payload(
        tool_calls=[
            {
                "id": "call_1",
                "function": {
                    "name": "get_weather",
                    "arguments": json.dumps({"city": "Tbilisi", "unit": "celsius"}),
                },
            }
        ]
    )

    [call] = parse_completion(payload).tool_calls

    assert (call.id, call.name) == ("call_1", "get_weather")
    assert call.arguments == {"city": "Tbilisi", "unit": "celsius"}


def test_several_tool_calls_keep_their_order() -> None:
    payload = completion_payload(
        tool_calls=[
            {"id": "a", "function": {"name": "list_files", "arguments": "{}"}},
            {"id": "b", "function": {"name": "read_file", "arguments": '{"path": "x"}'}},
        ]
    )

    calls = parse_completion(payload).tool_calls

    assert [call.name for call in calls] == ["list_files", "read_file"]
    assert calls[0].arguments == {}


def test_malformed_tool_arguments_are_delivered_with_their_text_kept() -> None:
    """One bad call is one bad result, not a failed request.

    The call reaches the loop with empty arguments and the text beside them;
    the executor is what refuses it, with the tool's signature. Raising here
    — which this did until 2026-09-03 — lost the whole turn over one call.
    """

    payload = completion_payload(
        tool_calls=[{"id": "a", "function": {"name": "read_file", "arguments": "{path: x"}}]
    )

    call = parse_completion(payload).tool_calls[0]

    assert call == ToolCall(id="a", name="read_file", arguments={}, raw_arguments="{path: x")


def test_tool_arguments_that_are_not_an_object_are_kept_as_text_too() -> None:
    payload = completion_payload(
        tool_calls=[{"id": "a", "function": {"name": "read_file", "arguments": '"x"'}}]
    )

    call = parse_completion(payload).tool_calls[0]

    assert call.arguments == {} and call.raw_arguments == '"x"'


def test_a_nameless_tool_call_is_refused() -> None:
    payload = completion_payload(tool_calls=[{"id": "a", "function": {"arguments": "{}"}}])

    with pytest.raises(BackendError, match="without a name"):
        parse_completion(payload)


def test_an_empty_response_is_refused() -> None:
    with pytest.raises(BackendError, match="no choices"):
        parse_completion({"choices": []})


@pytest.mark.parametrize(
    "line",
    ["data: [DONE]", "data:", "", ": keep-alive"],
)
def test_lines_that_are_not_chunks_carry_nothing(line: str) -> None:
    assert parse_stream_line(line) is None


def test_a_chunk_without_choices_is_still_a_chunk() -> None:
    """Usage arrives that way, and dropping it would drop the turn's size."""

    line = 'data: {"choices": [], "usage": {"prompt_tokens": 3}}'

    assert parse_stream_line(line) == {"choices": [], "usage": {"prompt_tokens": 3}}


# --- assembling a streamed completion ----------------------------------------
#
# The chunk shapes below were read off the deployed vLLM, not invented:
# reports/2026-08-29_v2_answer_streaming_preparation.md.


def assemble(chunks: list[dict[str, Any]]) -> Any:
    streamed = StreamedCompletion()
    text = "".join(streamed.add(chunk) for chunk in chunks)
    result = streamed.result()
    assert text == result.text  # what was shown is what was assembled
    return result


def delta(payload: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {"choices": [{"delta": payload, "finish_reason": finish}]}


def test_streamed_text_assembles_into_the_same_answer() -> None:
    result = assemble(
        [
            delta({"role": "assistant", "content": ""}),
            delta({"content": "One"}),
            delta({"content": ", two"}),
            delta({}, finish="stop"),
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 4,
                    "prompt_tokens_details": {"cached_tokens": 16},
                },
            },
        ]
    )

    assert result.text == "One, two"
    assert result.tool_calls == ()
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 21
    assert result.usage.output_tokens == 4
    # What the server served from its prefix cache: the one free measurement
    # of how well the request was assembled.
    assert result.usage.cached_tokens == 16


def test_reasoning_tokens_are_read_from_the_output_details() -> None:
    """A model that thinks first bills the thinking as output; the split is the measurement."""

    from app.models.openai_compatible import parse_usage

    usage = parse_usage(
        {
            "prompt_tokens": 25,
            "completion_tokens": 25,
            "completion_tokens_details": {"reasoning_tokens": 22},
        }
    )

    assert usage.output_tokens == 25
    assert usage.reasoning_tokens == 22
    assert parse_usage({"prompt_tokens": 1, "completion_tokens": 1}).reasoning_tokens is None


def test_a_leaked_end_of_turn_marker_is_not_text() -> None:
    """Run `9c42241c`, 2026-09-03: the last request answered `<eos>`, one token,
    and the person received it as a message."""

    result = assemble(
        [
            delta({"role": "assistant", "content": ""}),
            delta({"content": "<eos>"}),
            delta({}, finish="stop"),
        ]
    )
    assert result.text == ""

    trailing = assemble(
        [delta({"content": "Done."}), delta({"content": "<end_of_turn>"}), delta({}, finish="stop")]
    )
    assert trailing.text == "Done."


def test_a_fragmented_tool_call_assembles_into_one_call() -> None:
    result = assemble(
        [
            delta({"role": "assistant", "content": ""}),
            delta(
                {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "index": 0,
                            "function": {"name": "get_weather"},
                        }
                    ]
                }
            ),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"city": "'}}]}),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": "Paris"}}]}),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": '"}'}}]}),
            delta({}, finish="tool_calls"),
        ]
    )

    assert result.text == ""
    assert result.tool_calls == (
        ToolCall(id="call-1", name="get_weather", arguments={"city": "Paris"}),
    )
    assert result.finish_reason == "tool_calls"


def test_several_tool_calls_keep_their_positions() -> None:
    """Fragments interleave, so position is the only thing tying them together."""

    result = assemble(
        [
            delta({"tool_calls": [{"id": "a", "index": 0, "function": {"name": "first"}}]}),
            delta({"tool_calls": [{"id": "b", "index": 1, "function": {"name": "second"}}]}),
            delta({"tool_calls": [{"index": 1, "function": {"arguments": '{"n": 2}'}}]}),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"n": 1}'}}]}),
        ]
    )

    assert [(call.name, call.arguments) for call in result.tool_calls] == [
        ("first", {"n": 1}),
        ("second", {"n": 2}),
    ]


def test_text_and_a_tool_call_can_arrive_from_one_response() -> None:
    result = assemble(
        [
            delta({"content": "Paris is lovely."}),
            delta({"tool_calls": [{"id": "a", "index": 0, "function": {"name": "get_weather"}}]}),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}),
            delta({}, finish="tool_calls"),
        ]
    )

    assert result.text == "Paris is lovely."
    assert result.tool_calls[0].name == "get_weather"


def test_a_streamed_tool_call_without_a_name_is_refused() -> None:
    streamed = StreamedCompletion()
    streamed.add(delta({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}))

    with pytest.raises(BackendError, match="without a name"):
        streamed.result()


def test_streamed_arguments_that_are_not_json_are_delivered_unread() -> None:
    streamed = StreamedCompletion()
    streamed.add(delta({"tool_calls": [{"id": "a", "index": 0, "function": {"name": "cut"}}]}))
    streamed.add(delta({"tool_calls": [{"index": 0, "function": {"arguments": "{oops"}}]}))

    call = streamed.result().tool_calls[0]

    assert call.arguments == {} and call.raw_arguments == "{oops"


def test_reasoning_is_not_shown_and_does_not_break_assembly() -> None:
    """A server may split reasoning out; `invoke` ignores it and so does this."""

    result = assemble(
        [
            delta({"reasoning_content": "thinking about it"}),
            delta({"content": "Four."}),
            delta({}, finish="stop"),
        ]
    )

    assert result.text == "Four."


# --- requests ----------------------------------------------------------------


async def test_invoke_posts_a_complete_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion_payload(content="pong"))

    tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
    response_format = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}

    async with backend(handler, api_key="secret") as client:
        result = await client.invoke([Message(role="user", content=[text_part()])], tools, response_format)

    assert result.text == "pong"
    assert seen["url"] == "http://test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["tools"] == tools
    assert seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["response_format"] == response_format
    assert "stream" not in seen["body"]


async def test_modal_proxy_auth_sends_the_two_modal_headers() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["modal_key"] = request.headers.get("modal-key")
        seen["modal_secret"] = request.headers.get("modal-secret")
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(
        handler,
        api_key="wk-example.ws-example",
        auth_style="modal_proxy",
    ) as client:
        await client.invoke([Message(role="user", content=[text_part()])])

    assert seen == {
        "authorization": None,
        "modal_key": "wk-example",
        "modal_secret": "ws-example",
    }


@pytest.mark.parametrize("api_key", [None, "", "secret", "wk-example.bad-secret"])
def test_modal_proxy_auth_refuses_a_missing_or_malformed_token(api_key: str | None) -> None:
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        backend(lambda _request: httpx.Response(500), api_key=api_key, auth_style="modal_proxy")


def test_leading_system_messages_are_sent_as_one() -> None:
    # The prelude's layers are each a system message; the wire carries one.
    sent = build_messages(
        [
            Message(role="system", content=[text_part("You are the assistant.")]),
            Message(role="system", content=[text_part("Standing instructions.")]),
            Message(role="system", content=[text_part("Summary of the earlier conversation: apples.")]),
            Message(role="user", content=[text_part("hi")]),
        ]
    )
    assert [item["role"] for item in sent] == ["system", "user"]
    assert sent[0]["content"] == [
        {
            "type": "text",
            "text": "You are the assistant.\n\nStanding instructions.\n\nSummary of the earlier conversation: apples.",
        }
    ]


def test_a_system_message_after_history_rides_with_the_next_user_message() -> None:
    # The facts layer stands between the history and the turn: it stays there,
    # as the first text of the turn's user message, so a template that takes
    # one system message accepts it and the cached prefix before it holds.
    sent = build_messages(
        [
            Message(role="system", content=[text_part("You are the assistant.")]),
            Message(role="user", content=[text_part("earlier")]),
            Message(role="assistant", content=[text_part("yes")]),
            Message(role="system", content=[text_part("Facts you saved:\n- tea, no sugar")]),
            Message(role="user", content=[text_part("what do I take?")]),
        ]
    )
    assert [item["role"] for item in sent] == ["system", "user", "assistant", "user"]
    assert sent[-1]["content"][0] == {"type": "text", "text": "Facts you saved:\n- tea, no sugar"}
    assert sent[-1]["content"][1] == {"type": "text", "text": "what do I take?"}


def test_a_system_message_with_no_user_after_it_becomes_a_user_message() -> None:
    sent = build_messages(
        [
            Message(role="user", content=[text_part("go")]),
            Message(role="system", content=[text_part("A note from the harness.")]),
            Message(role="assistant", content=[text_part("done")]),
        ]
    )
    assert [item["role"] for item in sent] == ["user", "user", "assistant"]
    assert sent[1]["content"] == [{"type": "text", "text": "A note from the harness."}]


async def test_an_edge_redirect_is_followed_to_the_answer() -> None:
    """Modal's edge answers a request older than 150 s with a 303 to a URL that holds it."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "held=" not in str(request.url):
            return httpx.Response(303, headers={"Location": f"{request.url}?held=1"})
        return httpx.Response(200, json=completion_payload(content="after the wait"))

    async with backend(handler) as client:
        completion = await client.invoke([Message(role="user", content=[text_part()])])

    assert completion.text == "after the wait"
    assert len(seen) == 2 and "held=1" in seen[1]


async def test_context_limit_treats_a_non_json_answer_as_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>waiting</html>")

    async with backend(handler) as client:
        assert await client.context_limit() is None


async def test_context_limit_treats_a_redirect_it_cannot_follow_as_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(303, headers={"Location": str(request.url) + "?held=1"})

    async with backend(handler) as client:
        # Eight hops, all 303: httpx gives up with TooManyRedirects, an HTTPError.
        assert await client.context_limit() is None


async def test_chat_template_kwargs_are_sent_when_configured() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler, chat_template_kwargs={"enable_thinking": True, "reasoning_effort": "low"}) as client:
        await client.invoke([Message(role="user", content=[text_part()])])

    assert seen["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}


async def test_extra_body_is_merged_last() -> None:
    """A hosted service's own field goes in, and a field the client sets can be overridden."""

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler, extra_body={"tool_stream": True, "temperature": 0.3}) as client:
        await client.invoke([Message(role="user", content=[text_part()])])

    assert seen["tool_stream"] is True
    assert seen["temperature"] == 0.3


async def test_no_extra_body_by_default() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler) as client:
        await client.invoke([Message(role="user", content=[text_part()])])

    assert "tool_stream" not in seen
    assert seen["temperature"] == 0.0


async def test_no_chat_template_kwargs_by_default() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler) as client:
        await client.invoke([Message(role="user", content=[text_part()])])

    assert "chat_template_kwargs" not in seen


async def test_a_request_without_tools_omits_them() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler) as client:
        await client.invoke([Message(role="user", content=[text_part()])])

    assert "tools" not in seen
    assert "response_format" not in seen


async def test_stream_yields_text_in_order_and_then_the_completion() -> None:
    body = (
        "".join(
            f"data: {json.dumps(delta({'content': word}))}\n\n"
            for word in ("one", " two", " three")
        )
        + f"data: {json.dumps(delta({}, finish='stop'))}\n\n"
        + 'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}\n\n'
        + "data: [DONE]\n\n"
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=body.encode())

    async with backend(handler) as client:
        events = [
            event async for event in client.stream([Message(role="user", content=[text_part()])])
        ]

    assert events[:-1] == [TextDelta("one"), TextDelta(" two"), TextDelta(" three")]
    assert isinstance(events[-1], CompletionDone)
    assert events[-1].completion.text == "one two three"
    assert events[-1].completion.usage.input_tokens == 7
    assert events[-1].completion.finish_reason == "stop"
    assert seen["stream"] is True
    # Usage is not sent unless it is asked for, and a turn of unknown size
    # cannot be folded or reported.
    assert seen["stream_options"] == {"include_usage": True}


async def test_a_broken_stream_is_not_retried_and_yields_no_completion() -> None:
    """Half an answer has already been read; sending it twice is worse."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        async def content() -> Any:
            yield f"data: {json.dumps(delta({'content': 'partial'}))}\n\n".encode()
            raise httpx.ReadError("the connection dropped")

        return httpx.Response(200, content=content())

    seen: list[Any] = []
    async with backend(handler) as client:
        with pytest.raises(httpx.ReadError):
            async for event in client.stream([Message(role="user", content=[text_part()])]):
                seen.append(event)

    assert seen == [TextDelta("partial")]
    assert attempts == 1


async def test_a_stream_that_timed_out_is_not_sent_again() -> None:
    """ISS-0044: the request is still queued at the endpoint; a second copy is waste."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("no byte within the timeout")

    async with backend(handler, retries=2, retry_backoff=0.0) as client:
        with pytest.raises(httpx.ReadTimeout):
            async for _ in client.stream([Message(role="user", content=[text_part()])]):
                pass

    assert attempts == 1


async def test_a_stream_refused_by_the_transport_is_sent_again() -> None:
    attempts = 0
    body = (
        f"data: {json.dumps(delta({'content': 'awake'}))}\n\n"
        + f"data: {json.dumps(delta({}, finish='stop'))}\n\n"
        + "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, content=body.encode())

    async with backend(handler, retries=2, retry_backoff=0.0) as client:
        events = [
            event async for event in client.stream([Message(role="user", content=[text_part()])])
        ]

    assert attempts == 2
    assert events[0] == TextDelta("awake")
    assert isinstance(events[-1], CompletionDone)


async def test_a_stream_refused_with_a_later_status_is_sent_again() -> None:
    attempts = 0
    body = f"data: {json.dumps(delta({'content': 'now'}))}\n\ndata: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="starting")
        return httpx.Response(200, content=body.encode())

    async with backend(handler, retries=2, retry_backoff=0.0) as client:
        events = [
            event async for event in client.stream([Message(role="user", content=[text_part()])])
        ]

    assert attempts == 2
    assert events[0] == TextDelta("now")


async def test_a_completion_that_timed_out_is_not_sent_again() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("still booting")

    async with backend(handler, retries=2, retry_backoff=0.0) as client:
        with pytest.raises(BackendError, match="ReadTimeout"):
            await client.invoke([Message(role="user", content=[text_part()])])

    assert attempts == 1


async def test_an_http_error_becomes_a_backend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    async with backend(handler) as client:
        with pytest.raises(BackendError, match="HTTP 400"):
            await client.invoke([Message(role="user", content=[text_part()])])


@pytest.mark.parametrize(
    "detail",
    [
        "This model's maximum context length is 8192 tokens",
        "prompt is too long for max_model_len",
        "context window exceeded",
    ],
)
def test_context_overflow_wording_is_classified_at_the_provider_boundary(detail: str) -> None:
    assert is_context_overflow(400, detail)


def test_an_unrelated_bad_request_is_not_classified_as_context_overflow() -> None:
    assert not is_context_overflow(400, "invalid image payload")
    assert not is_context_overflow(503, "maximum context length")


async def test_context_overflow_has_a_typed_backend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "This model's maximum context length is 8192 tokens"},
        )

    async with backend(handler) as client:
        with pytest.raises(ContextOverflowError, match="HTTP 400"):
            await client.invoke([Message(role="user", content=[text_part()])])


# --- retries -----------------------------------------------------------------


def ask(client: OpenAICompatibleBackend):
    return client.invoke([Message(role="user", content=[text_part()])])


async def test_a_busy_server_is_tried_again_and_the_answer_arrives() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="starting up")
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler, retries=2, retry_backoff=0) as client:
        assert (await ask(client)).text == "pong"

    assert len(attempts) == 3


async def test_a_server_that_never_recovers_fails_after_the_last_attempt() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, text="still starting")

    async with backend(handler, retries=2, retry_backoff=0) as client:
        with pytest.raises(BackendError, match="HTTP 503"):
            await ask(client)

    assert len(attempts) == 3


async def test_a_rejected_request_is_not_repeated() -> None:
    """A 400 is about the request, and the request will not improve."""

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"error": "bad request"})

    async with backend(handler, retries=2, retry_backoff=0) as client:
        with pytest.raises(BackendError, match="HTTP 400"):
            await ask(client)

    assert len(attempts) == 1


async def test_a_dropped_connection_is_tried_again() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=completion_payload(content="pong"))

    async with backend(handler, retries=2, retry_backoff=0) as client:
        assert (await ask(client)).text == "pong"

    assert len(attempts) == 2


async def test_a_server_that_stays_down_is_reported_as_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with backend(handler, retries=1, retry_backoff=0) as client:
        with pytest.raises(BackendError, match="could not be reached"):
            await ask(client)


async def test_an_unreachable_server_is_named_and_so_is_the_failure() -> None:
    """A timeout stringifies to nothing, so the message must not rely on it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("")

    async with backend(handler, retries=0, retry_backoff=0) as client:
        with pytest.raises(BackendError) as raised:
            await ask(client)

    assert "ConnectTimeout" in str(raised.value)
    assert "http://test/v1" in str(raised.value)


# --- the context limit -------------------------------------------------------


def test_the_limit_is_read_from_the_named_model() -> None:
    payload = {
        "data": [
            {"id": "other-model", "max_model_len": 4096},
            {"id": "test-model", "max_model_len": 16384},
        ]
    }

    assert parse_context_limit(payload, "test-model") == 16384


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"id": "test-model"}]},
        {"data": [{"id": "someone-else", "max_model_len": 16384}]},
        {},
    ],
    ids=["not reported", "another model", "empty listing"],
)
def test_a_limit_that_is_not_stated_is_unknown(payload: dict[str, Any]) -> None:
    assert parse_context_limit(payload, "test-model") is None


async def test_the_backend_asks_the_server_for_its_limit() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": "test-model", "max_model_len": 16384}]})

    async with backend(handler) as client:
        assert await client.context_limit() == 16384

    assert seen["url"] == "http://test/v1/models"


async def test_an_unreachable_server_leaves_the_limit_unknown() -> None:
    """Not an error: the model call that follows is what reports a dead server."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with backend(handler) as client:
        assert await client.context_limit() is None

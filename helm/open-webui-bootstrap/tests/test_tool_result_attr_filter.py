"""
Unit tests for tool_result_attr_filter.

Run with:
    cd helm/open-webui-bootstrap
    PYTHONPATH=files/payloads uvx --with pytest-asyncio --with pydantic \
        pytest tests/test_tool_result_attr_filter.py -v
"""

import html
import json

import pytest

from tool_result_attr_filter import Filter, rewrite_tool_calls_to_attribute


# Exactly what middleware.serialize_output emits for a completed tool call.
def _make_block(name: str, args: dict, result_text: str) -> str:
    return (
        f'<details type="tool_calls" done="true" id="call_x" '
        f'name="{name}" arguments="{html.escape(json.dumps(args))}" '
        f'files="" embeds="">\n'
        f"<summary>Tool Executed</summary>\n"
        f"{html.escape(json.dumps(result_text, ensure_ascii=False))}\n"
        f"</details>"
    )


class TestRewrite:
    def test_moves_body_to_result_attribute(self):
        block = _make_block("scout-db_execute_query", {"query": "SELECT 1"}, "abc")
        out = rewrite_tool_calls_to_attribute(block)
        assert 'result="' in out
        assert "<summary>Tool Executed</summary>\n</details>" in out
        # Body content gone from inside the block
        assert "&quot;abc&quot;" not in out.split('result="')[0]
        # ...but present inside the result attribute
        assert "&quot;abc&quot;" in out

    def test_idempotent(self):
        block = _make_block("scout-db_execute_query", {"query": "SELECT 1"}, "abc")
        once = rewrite_tool_calls_to_attribute(block)
        twice = rewrite_tool_calls_to_attribute(once)
        assert once == twice

    def test_leaves_reasoning_blocks_alone(self):
        reasoning = (
            '<details type="reasoning" done="true" duration="5">\n'
            "<summary>Thought for 5s</summary>\n"
            "The user wants a chart.\n"
            "</details>"
        )
        assert rewrite_tool_calls_to_attribute(reasoning) == reasoning

    def test_handles_multiple_consecutive_tool_calls(self):
        a = _make_block("scout-db_execute_query", {"query": "SELECT 1"}, "row1")
        b = _make_block("scout-db_execute_query", {"query": "SELECT 2"}, "row2")
        joined = a + "\n" + b
        out = rewrite_tool_calls_to_attribute(joined)
        # Two result= attributes now exist; both bodies migrated
        assert out.count("result=") == 2
        assert "&quot;row1&quot;" in out
        assert "&quot;row2&quot;" in out

    def test_handles_mixed_reasoning_and_tool_calls(self):
        reasoning = (
            '<details type="reasoning" done="true" duration="2">\n'
            "<summary>Thought</summary>\n"
            "Picking a query.\n"
            "</details>"
        )
        tc = _make_block("scout-db_execute_query", {"query": "SELECT 1"}, "row1")
        out = rewrite_tool_calls_to_attribute(reasoning + "\n" + tc)
        # Reasoning preserved exactly
        assert "Picking a query." in out
        # Tool call rewritten
        assert "result=" in out


class FakeEmitter:
    def __init__(self) -> None:
        self.events = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_outlet_rewrites_assistant_message_and_emits_replace():
    block = _make_block("scout-db_execute_query", {"query": "SELECT 1"}, "abc")
    body = {"messages": [{"role": "assistant", "content": block}]}
    emitter = FakeEmitter()
    out = await Filter().outlet(body, __event_emitter__=emitter)
    assert 'result="' in out["messages"][0]["content"]
    assert len(emitter.events) == 1
    assert emitter.events[0]["type"] == "replace"


@pytest.mark.asyncio
async def test_outlet_skips_user_messages():
    block = _make_block("scout-db_execute_query", {"query": "SELECT 1"}, "abc")
    body = {"messages": [{"role": "user", "content": block}]}
    emitter = FakeEmitter()
    out = await Filter().outlet(body, __event_emitter__=emitter)
    # User messages are not rewritten
    assert out["messages"][0]["content"] == block
    assert emitter.events == []


@pytest.mark.asyncio
async def test_outlet_no_op_when_already_attribute_format():
    # Already-rewritten content shouldn't trigger another emit
    attr_format = (
        '<details type="tool_calls" done="true" id="call_x" '
        'name="x" arguments="{}" files="" embeds="" result="&quot;abc&quot;">\n'
        "<summary>Tool Executed</summary>\n"
        "</details>"
    )
    body = {"messages": [{"role": "assistant", "content": attr_format}]}
    emitter = FakeEmitter()
    await Filter().outlet(body, __event_emitter__=emitter)
    # The pre-check (`<details type="tool_calls"` in content) matches, but the
    # rewrite finds the result= attribute is already present, so content is
    # unchanged AND no emit happens.
    assert emitter.events == []

"""
Unit tests for context_summarization_filter.

Run with:
    cd ansible/roles/open-webui/files
    uvx --with tiktoken --with httpx --with pydantic --with pytest-asyncio pytest test_context_summarization_filter.py -v

Message Format (verified via debug filter 2024-12):
    The inlet() function receives messages in a simplified format:
    - Only user/assistant/system roles (no role:"tool" messages)
    - Tool results are embedded directly in assistant message content
    - Tool results appear as escaped JSON with HTML entities (&quot; etc.)
    - No tool_calls array in assistant messages
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from context_summarization_filter import Filter


@pytest.fixture
def filter_instance():
    """Create a fresh filter instance for each test."""
    return Filter()


@pytest.fixture
def filter_with_low_threshold():
    """Filter configured with low token threshold for testing."""
    f = Filter()
    f.valves.token_threshold = 100  # Low threshold for testing
    f.valves.messages_to_keep = 2
    return f


# Sample embedded tool result content (matches actual Open WebUI format)
SAMPLE_TOOL_RESULT_CONTENT = """
"&quot;{\\n  \\&quot;results\\&quot;: [\\n    {\\n      \\&quot;epic_mrn\\&quot;: \\&quot;EPIC123\\&quot;,\\n      \\&quot;report_text\\&quot;: \\&quot;Sample report\\&quot;\\n    },\\n    {\\n      \\&quot;epic_mrn\\&quot;: \\&quot;EPIC456\\&quot;,\\n      \\&quot;report_text\\&quot;: \\&quot;Another report\\&quot;\\n    }\\n  ]\\n}&quot;"

Here are the results from the database query.
"""

SAMPLE_ERROR_CONTENT = """
"&quot;{\\n  \\&quot;error\\&quot;: \\&quot;Connection timeout to database server\\&quot;\\n}&quot;"
"""


class TestTokenCounting:
    """Test token counting functionality."""

    def test_count_tokens_empty(self, filter_instance):
        """Empty string returns 0 tokens."""
        assert filter_instance.count_tokens("") == 0
        assert filter_instance.count_tokens(None) == 0

    def test_count_tokens_simple(self, filter_instance):
        """Simple text returns expected token count."""
        result = filter_instance.count_tokens("Hello world")
        assert result > 0
        assert result < 10

    def test_count_tokens_longer_text(self, filter_instance):
        """Longer text returns higher token count."""
        short = filter_instance.count_tokens("Hi")
        long = filter_instance.count_tokens(
            "This is a much longer sentence with many more words."
        )
        assert long > short

    def test_count_message_tokens_string_content(self, filter_instance):
        """Count tokens in message with string content."""
        msg = {"role": "user", "content": "Hello world"}
        result = filter_instance.count_message_tokens(msg)
        assert result > 0

    def test_count_message_tokens_empty_content(self, filter_instance):
        """Message with empty content returns 0 tokens."""
        msg = {"role": "user", "content": ""}
        result = filter_instance.count_message_tokens(msg)
        assert result == 0

    def test_count_message_tokens_multimodal_content(self, filter_instance):
        """Count tokens in message with array content (multimodal)."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,..."},
                },
            ],
        }
        result = filter_instance.count_message_tokens(msg)
        assert result > 0

    def test_count_all_tokens(self, filter_instance):
        """Count tokens across multiple messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = filter_instance.count_all_tokens(messages)
        assert result > 0

    def test_count_all_tokens_empty_list(self, filter_instance):
        """Empty message list returns 0 tokens."""
        assert filter_instance.count_all_tokens([]) == 0


class TestExtractBaseSystemPrompt:
    """Test system prompt extraction."""

    def test_extract_with_system_first(self, filter_instance):
        """Extract system message when it's first."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        system, rest = filter_instance.extract_base_system_prompt(messages)
        assert len(system) == 1
        assert system[0]["content"] == "You are helpful."
        assert len(rest) == 1

    def test_extract_without_system_first(self, filter_instance):
        """No extraction when first message isn't system."""
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        system, rest = filter_instance.extract_base_system_prompt(messages)
        assert len(system) == 0
        assert len(rest) == 2

    def test_extract_empty_messages(self, filter_instance):
        """Handle empty message list."""
        system, rest = filter_instance.extract_base_system_prompt([])
        assert system == []
        assert rest == []

    def test_extract_only_first_system(self, filter_instance):
        """Only extract first system message, leave subsequent ones."""
        messages = [
            {"role": "system", "content": "Base prompt"},
            {"role": "system", "content": "RAG content"},
            {"role": "user", "content": "Hi"},
        ]
        system, rest = filter_instance.extract_base_system_prompt(messages)
        assert len(system) == 1
        assert system[0]["content"] == "Base prompt"
        assert len(rest) == 2
        assert rest[0]["role"] == "system"
        assert rest[0]["content"] == "RAG content"


class TestHasEmbeddedToolResult:
    """Test detection of embedded tool results in assistant content."""

    def test_detect_html_escaped_results(self, filter_instance):
        """Detect HTML-escaped JSON with results."""
        content = "&quot;results&quot;: [{...}]"
        assert filter_instance.has_embedded_tool_result(content) is True

    def test_detect_double_escaped_results(self, filter_instance):
        """Detect double-escaped JSON."""
        content = "\\&quot;results\\&quot;: [{...}]"
        assert filter_instance.has_embedded_tool_result(content) is True

    def test_detect_quoted_results(self, filter_instance):
        """Detect quoted results pattern."""
        content = '\\"results\\": [{...}]'
        assert filter_instance.has_embedded_tool_result(content) is True

    def test_detect_json_array_start(self, filter_instance):
        """Detect JSON array at start."""
        content = '[{"epic_mrn": "123"}]'
        assert filter_instance.has_embedded_tool_result(content) is True

    def test_detect_escaped_quote_start(self, filter_instance):
        """Detect escaped quote at start (common Open WebUI pattern)."""
        content = '"&quot;{\\n  \\&quot;results\\&quot;'
        assert filter_instance.has_embedded_tool_result(content) is True

    def test_no_detection_plain_text(self, filter_instance):
        """Don't detect in plain assistant text."""
        content = "Here is my response to your question about the data."
        assert filter_instance.has_embedded_tool_result(content) is False

    def test_no_detection_empty(self, filter_instance):
        """Handle empty content."""
        assert filter_instance.has_embedded_tool_result("") is False
        assert filter_instance.has_embedded_tool_result(None) is False

    def test_detect_actual_format(self, filter_instance):
        """Detect actual Open WebUI format."""
        assert (
            filter_instance.has_embedded_tool_result(SAMPLE_TOOL_RESULT_CONTENT) is True
        )


class TestExtractToolResultInfo:
    """Test extraction of tool result information."""

    def test_extract_from_empty(self, filter_instance):
        """Handle empty content."""
        info = filter_instance.extract_tool_result_info("")
        assert info["has_results"] is False
        assert info["result_count"] is None

    def test_extract_from_json_array(self, filter_instance):
        """Extract info from JSON array."""
        content = '[{"a": 1}, {"a": 2}, {"a": 3}]'
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_results"] is True
        assert info["result_count"] == 3

    def test_extract_from_results_object(self, filter_instance):
        """Extract info from object with results key."""
        content = '{"results": [{"id": 1}, {"id": 2}]}'
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_results"] is True
        assert info["result_count"] == 2

    def test_extract_from_rows_object(self, filter_instance):
        """Extract info from object with rows key."""
        content = '{"rows": [{"col": "a"}, {"col": "b"}, {"col": "c"}, {"col": "d"}]}'
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_results"] is True
        assert info["result_count"] == 4

    def test_extract_error(self, filter_instance):
        """Extract error information from query execution failed pattern."""
        # Need duplicated pattern since real messages have it twice
        content = "query execution failed: query execution failed: Connection failed to database"
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_error"] is True
        assert info["error_count"] >= 1

    def test_extract_from_html_escaped(self, filter_instance):
        """Extract from HTML-escaped content."""
        content = "&quot;results&quot;: [1, 2, 3]"
        info = filter_instance.extract_tool_result_info(content)
        # May or may not parse depending on exact format, but should detect
        assert info["has_results"] is True

    def test_fallback_pattern_counting(self, filter_instance):
        """Fall back to pattern counting when JSON parsing fails."""
        # Content that matches detection patterns but contains invalid JSON
        # This has the &quot;results&quot; pattern but the JSON is malformed
        content = "&quot;results&quot;: [not valid json &quot;epic_mrn&quot;: &quot;123&quot;, &quot;message_dt&quot;: &quot;2023&quot;"
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_results"] is True


class TestCompactAssistantWithToolResult:
    """Test compacting assistant messages with embedded tool results."""

    def test_compact_with_count_and_sample(self, filter_instance):
        """Compact message showing row count and sample row."""
        msg = {
            "role": "assistant",
            "content": '[{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}, {"name": "Carol", "age": 35}]',
        }
        result = filter_instance.compact_assistant_with_tool_result(msg)
        assert result["role"] == "assistant"
        assert "[Tool:" in result["content"]
        assert "3 rows" in result["content"]
        # Should include sample row with first item's data
        assert "Alice" in result["content"]

    def test_compact_with_error_count(self, filter_instance):
        """Compact message showing failed query count."""
        msg = {
            "role": "assistant",
            "content": "query execution failed: Database error\nquery execution failed: Another error",
        }
        result = filter_instance.compact_assistant_with_tool_result(msg)
        assert "[Tool:" in result["content"]
        assert "failed" in result["content"]

    def test_compact_preserves_commentary(self, filter_instance):
        """Preserve assistant commentary after tool result."""
        content = '{"results": [{"x": 1}, {"x": 2}]}" \n\nHere is my detailed analysis of the query results.'
        msg = {"role": "assistant", "content": content}
        result = filter_instance.compact_assistant_with_tool_result(msg)
        assert "[Tool:" in result["content"]
        assert "2 rows" in result["content"]
        assert "analysis" in result["content"]

    def test_compact_empty_content(self, filter_instance):
        """Handle empty content."""
        msg = {"role": "assistant", "content": ""}
        result = filter_instance.compact_assistant_with_tool_result(msg)
        assert result == msg

    def test_compact_large_content_fallback(self, filter_instance):
        """Fall back to character count for unparseable content."""
        content = "x" * 10000  # Large unparseable content
        # Manually make it detectable as tool result
        content = "&quot;results&quot;" + content
        msg = {"role": "assistant", "content": content}
        result = filter_instance.compact_assistant_with_tool_result(msg)
        assert "[Tool:" in result["content"]
        # Should show either "results returned" or character count
        assert "results" in result["content"] or "chars" in result["content"]


class TestSplitConversation:
    """Test conversation splitting."""

    def test_split_basic(self, filter_instance):
        """Basic split keeps specified number of messages."""
        messages = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "4"},
        ]
        old, recent = filter_instance.split_conversation(messages, 2)
        assert len(old) == 2
        assert len(recent) == 2
        assert recent[0]["content"] == "3"

    def test_split_fewer_than_keep_count(self, filter_instance):
        """When fewer messages than keep count, all go to recent."""
        messages = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
        ]
        old, recent = filter_instance.split_conversation(messages, 5)
        assert len(old) == 0
        assert len(recent) == 2


class TestFindDynamicSplit:
    """Test dynamic split point finding."""

    def test_dynamic_split_normal_case(self, filter_instance):
        """Normal case finds split point."""
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        old, recent = filter_instance.find_dynamic_split(messages, 10)
        assert len(old) == 10
        assert len(recent) == 10

    def test_dynamic_split_reduces_keep_count(self, filter_instance):
        """Reduces keep count when needed."""
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(5)]
        # Initial keep of 10 would leave nothing to summarize
        old, recent = filter_instance.find_dynamic_split(messages, 10, min_keep=2)
        assert len(old) > 0 or len(recent) == len(messages)

    def test_dynamic_split_respects_min_keep(self, filter_instance):
        """Respects minimum keep count."""
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(4)]
        old, recent = filter_instance.find_dynamic_split(messages, 10, min_keep=2)
        assert len(recent) >= 2

    def test_dynamic_split_all_recent(self, filter_instance):
        """When can't split, all messages are recent."""
        messages = [{"role": "user", "content": "only one"}]
        old, recent = filter_instance.find_dynamic_split(messages, 10, min_keep=2)
        assert len(old) == 0
        assert len(recent) == 1


class TestPrepareForSummarization:
    """Test message preparation for summarization."""

    def test_prepare_user_assistant_messages(self, filter_instance):
        """User and assistant messages pass through."""
        messages = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        prepared, tool_summaries = filter_instance.prepare_for_summarization(messages)
        assert len(prepared) == 2
        assert prepared[0]["content"] == "question"
        assert prepared[1]["content"] == "answer"
        assert len(tool_summaries) == 0

    def test_prepare_skips_system_messages(self, filter_instance):
        """System messages are skipped (RAG will re-retrieve)."""
        messages = [
            {"role": "system", "content": "RAG content"},
            {"role": "user", "content": "question"},
        ]
        prepared, tool_summaries = filter_instance.prepare_for_summarization(messages)
        assert len(prepared) == 1
        assert prepared[0]["role"] == "user"

    def test_prepare_compacts_large_tool_results(self, filter_instance):
        """Large embedded tool results are compacted and tool summaries extracted."""
        # Create a message with embedded tool result exceeding threshold
        large_result = (
            "[" + ",".join(['{"x": ' + str(i) + "}" for i in range(100)]) + "]"
        )
        messages = [
            {"role": "assistant", "content": large_result},
        ]
        filter_instance.valves.tool_result_token_threshold = 50
        prepared, tool_summaries = filter_instance.prepare_for_summarization(messages)
        assert len(prepared) == 1
        assert "[Tool:" in prepared[0]["content"]
        assert "100 rows" in prepared[0]["content"]
        # Should also extract tool summary
        assert len(tool_summaries) == 1
        assert "100 rows" in tool_summaries[0]

    def test_prepare_keeps_small_tool_results(self, filter_instance):
        """Small tool results are kept as-is."""
        small_result = '[{"x": 1}]'
        messages = [
            {"role": "assistant", "content": small_result},
        ]
        filter_instance.valves.tool_result_token_threshold = 1000
        prepared, tool_summaries = filter_instance.prepare_for_summarization(messages)
        assert len(prepared) == 1
        assert prepared[0]["content"] == small_result
        # No tool summary since it wasn't compacted
        assert len(tool_summaries) == 0


class TestSummarizeMessages:
    """Test message summarization."""

    @pytest.mark.asyncio
    async def test_summarize_basic(self, filter_instance):
        """Basic summarization calls Ollama API."""
        messages = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "2+2 equals 4."},
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "User asked about addition."}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_instance.summarize_messages(messages, "test-model")
            assert result == "User asked about addition."

    @pytest.mark.asyncio
    async def test_summarize_empty_messages(self, filter_instance):
        """Empty messages return empty summary."""
        result = await filter_instance.summarize_messages([], "test-model")
        assert result == ""

    @pytest.mark.asyncio
    async def test_summarize_skips_empty_content(self, filter_instance):
        """Messages with empty content are skipped."""
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "response"},
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Summary"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_instance.summarize_messages(messages, "test-model")
            assert result == "Summary"


class TestInlet:
    """Test the main inlet function."""

    @pytest.mark.asyncio
    async def test_inlet_below_threshold_passthrough(self, filter_instance):
        """Messages below threshold pass through unchanged."""
        body = {
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ],
            "model": "test-model",
        }
        result = await filter_instance.inlet(body)
        assert result == body

    @pytest.mark.asyncio
    async def test_inlet_empty_messages(self, filter_instance):
        """Empty messages pass through."""
        body = {"messages": [], "model": "test-model"}
        result = await filter_instance.inlet(body)
        assert result == body

    @pytest.mark.asyncio
    async def test_inlet_no_messages_key(self, filter_instance):
        """Missing messages key passes through."""
        body = {"model": "test-model"}
        result = await filter_instance.inlet(body)
        assert result == body

    @pytest.mark.asyncio
    async def test_inlet_above_threshold_summarizes(self, filter_with_low_threshold):
        """Messages above threshold trigger summarization."""
        # Create enough messages to exceed low threshold
        messages = [
            {"role": "user", "content": f"Message {i} " * 20} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Conversation summary here."}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_with_low_threshold.inlet(body)

            # Should have fewer messages now
            assert len(result["messages"]) < len(messages)
            # Should include a summary message
            has_summary = any(
                "[Previous conversation summary]" in m.get("content", "")
                for m in result["messages"]
            )
            assert has_summary

    @pytest.mark.asyncio
    async def test_inlet_preserves_recent_messages(self, filter_with_low_threshold):
        """Recent messages are preserved intact."""
        messages = [
            {"role": "user", "content": f"Old message {i} " * 30} for i in range(5)
        ] + [
            {"role": "user", "content": "Recent message 1"},
            {"role": "assistant", "content": "Recent response 1"},
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Summary"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_with_low_threshold.inlet(body)

            # Recent messages should be at the end
            assert result["messages"][-1]["content"] == "Recent response 1"
            assert result["messages"][-2]["content"] == "Recent message 1"

    @pytest.mark.asyncio
    async def test_inlet_emits_status_events(self, filter_with_low_threshold):
        """Status events are emitted during summarization."""
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}
        event_emitter = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Summary"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            await filter_with_low_threshold.inlet(body, __event_emitter__=event_emitter)

            # Should have emitted at least 2 status events (start and done)
            assert event_emitter.call_count >= 2

    @pytest.mark.asyncio
    async def test_inlet_uses_summarizer_model_if_set(self, filter_with_low_threshold):
        """Uses summarizer_model valve if set."""
        filter_with_low_threshold.valves.summarizer_model = "summarizer-model"
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "chat-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Summary"}
            mock_post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.post = mock_post

            await filter_with_low_threshold.inlet(body)

            # Check the model used in API call
            call_args = mock_post.call_args
            assert call_args[1]["json"]["model"] == "summarizer-model"

    @pytest.mark.asyncio
    async def test_inlet_uses_chat_model_if_summarizer_not_set(
        self, filter_with_low_threshold
    ):
        """Uses chat model if summarizer_model not set."""
        filter_with_low_threshold.valves.summarizer_model = ""
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "chat-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Summary"}
            mock_post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.post = mock_post

            await filter_with_low_threshold.inlet(body)

            call_args = mock_post.call_args
            assert call_args[1]["json"]["model"] == "chat-model"

    @pytest.mark.asyncio
    async def test_inlet_nothing_to_summarize(self, filter_with_low_threshold):
        """When nothing to summarize, pass through with status."""
        # Only 2 messages but configured to keep 2
        messages = [
            {"role": "user", "content": "x" * 200},  # Exceeds threshold
            {"role": "assistant", "content": "y" * 200},
        ]
        body = {"messages": messages, "model": "test-model"}
        event_emitter = AsyncMock()

        result = await filter_with_low_threshold.inlet(
            body, __event_emitter__=event_emitter
        )

        # Should pass through unchanged
        assert result == body


class TestValvesConfiguration:
    """Test valve configuration."""

    def test_default_valves(self, filter_instance):
        """Default valve values are set."""
        assert filter_instance.valves.token_threshold == 100000
        assert filter_instance.valves.messages_to_keep == 10
        assert filter_instance.valves.min_messages_to_keep == 2
        assert filter_instance.valves.ollama_url == "http://ollama:11434"
        assert filter_instance.valves.summarizer_model == ""
        assert filter_instance.valves.tool_result_token_threshold == 500

    def test_custom_token_threshold(self):
        """Can set custom token threshold."""
        f = Filter()
        f.valves.token_threshold = 50000
        assert f.valves.token_threshold == 50000

    def test_custom_messages_to_keep(self):
        """Can set custom messages to keep."""
        f = Filter()
        f.valves.messages_to_keep = 5
        assert f.valves.messages_to_keep == 5

    def test_custom_ollama_url(self):
        """Can set custom Ollama URL."""
        f = Filter()
        f.valves.ollama_url = "http://localhost:11434"
        assert f.valves.ollama_url == "http://localhost:11434"

    def test_custom_tool_result_threshold(self):
        """Can set custom tool result token threshold."""
        f = Filter()
        f.valves.tool_result_token_threshold = 1000
        assert f.valves.tool_result_token_threshold == 1000


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_multimodal_content_token_counting(self, filter_instance):
        """Handle multimodal content in token counting."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        }
        result = filter_instance.count_message_tokens(msg)
        assert result > 0

    def test_very_long_message(self, filter_instance):
        """Handle very long messages."""
        long_content = "word " * 10000
        msg = {"role": "user", "content": long_content}
        result = filter_instance.count_message_tokens(msg)
        assert result > 1000

    @pytest.mark.asyncio
    async def test_summarization_api_error_graceful_degradation(
        self, filter_with_low_threshold
    ):
        """Handle API errors gracefully with fallback to truncation."""
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("API Error")
            )

            # Should NOT raise - graceful degradation
            result = await filter_with_low_threshold.inlet(body)

            # Should have truncated messages with fallback note
            assert len(result["messages"]) < len(messages)
            # Should have fallback note about truncation
            has_truncation_note = any(
                "truncated" in m.get("content", "").lower() for m in result["messages"]
            )
            assert has_truncation_note

    def test_unicode_content(self, filter_instance):
        """Handle unicode content correctly."""
        msg = {"role": "user", "content": "Hello 你好 مرحبا 🎉"}
        result = filter_instance.count_message_tokens(msg)
        assert result > 0

    def test_nested_json_in_tool_result(self, filter_instance):
        """Handle nested JSON in tool results."""
        content = '{"results": [{"nested": {"deep": [1, 2, 3]}}]}'
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_results"] is True
        assert info["result_count"] == 1


class TestActualOpenWebUIFormat:
    """Test with actual Open WebUI message format from captured data."""

    def test_detect_actual_tool_result(self, filter_instance):
        """Detect tool result in actual Open WebUI format."""
        # Actual format from captured inlet body
        content = """
"&quot;{\\n  \\&quot;results\\&quot;: [\\n    {\\n      \\&quot;epic_mrn\\&quot;: \\&quot;EPIC9262860\\&quot;,\\n      \\&quot;message_dt\\&quot;: \\&quot;2023-05-16T15:16:42Z\\&quot;,\\n      \\&quot;report_text\\&quot;: \\&quot;EXAMINATION: MRI...\\&quot;\\n    }\\n  ]\\n}&quot;"

Here are ten reports with typos.
"""
        assert filter_instance.has_embedded_tool_result(content) is True

    def test_extract_info_from_actual_format(self, filter_instance):
        """Extract info from actual Open WebUI format."""
        content = SAMPLE_TOOL_RESULT_CONTENT
        info = filter_instance.extract_tool_result_info(content)
        assert info["has_results"] is True

    def test_compact_actual_format(self, filter_instance):
        """Compact message in actual Open WebUI format."""
        msg = {"role": "assistant", "content": SAMPLE_TOOL_RESULT_CONTENT}
        result = filter_instance.compact_assistant_with_tool_result(msg)
        assert result["role"] == "assistant"
        assert "[Tool:" in result["content"]
        # Should preserve the commentary after the JSON
        assert "results from the database" in result["content"]


class TestRealWorldFormat:
    """Test with actual Open WebUI message format from production."""

    def test_extract_from_real_trino_response(self, filter_instance):
        """Extract info from real Trino MCP response with errors and results."""
        # Actual format uses \&quot; not \\&quot; (single backslash in raw string)
        content = (
            r'"&quot;[{&#x27;type&#x27;: &#x27;text&#x27;, &#x27;text&#x27;: &#x27;query execution failed: query execution failed: trino: query failed&#x27;}]&quot;"'
            + "\n\n"
            + r'"&quot;{\n  \&quot;results\&quot;: [\n    {\n      \&quot;diagnosis\&quot;: \&quot;Malignant neoplasm of lung\&quot;,\n      \&quot;patient_count\&quot;: 5\n    },\n    {\n      \&quot;diagnosis\&quot;: \&quot;Brain tumor\&quot;,\n      \&quot;patient_count\&quot;: 3\n    }\n  ]\n}&quot;"'
            + "\n\n**Results table here**"
        )

        info = filter_instance.extract_tool_result_info(content)
        assert info["has_error"] is True
        assert info["error_count"] >= 1
        assert info["has_results"] is True
        assert info["result_count"] == 2
        assert info["sample_row"] is not None
        assert "diagnosis" in info["sample_row"]

    def test_compact_real_trino_response(self, filter_instance):
        """Compact real Trino response with errors, results, and commentary."""
        content = (
            r'"&quot;[{&#x27;text&#x27;: &#x27;query execution failed: query execution failed: error&#x27;}]&quot;"'
            + "\n\n"
            + r'"&quot;{\n  \&quot;results\&quot;: [\n    {\n      \&quot;modality\&quot;: \&quot;CT\&quot;,\n      \&quot;count\&quot;: 1234\n    },\n    {\n      \&quot;modality\&quot;: \&quot;MRI\&quot;,\n      \&quot;count\&quot;: 567\n    }\n  ]\n}&quot;"'
            + """

**Most common modalities:**

| Modality | Count |
|----------|-------|
| CT | 1234 |
| MRI | 567 |

The data shows CT is most common.
"""
        )
        msg = {"role": "assistant", "content": content}
        result = filter_instance.compact_assistant_with_tool_result(msg)

        assert "[Tool:" in result["content"]
        assert "2 rows" in result["content"]
        # Should have sample data
        assert "CT" in result["content"] or "modality" in result["content"]
        # Should preserve commentary
        assert "common" in result["content"].lower()


class TestGracefulDegradation:
    """Test graceful degradation when summarization fails."""

    @pytest.mark.asyncio
    async def test_empty_summary_triggers_fallback(self, filter_with_low_threshold):
        """Empty summary from API triggers fallback to truncation."""
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": ""}  # Empty summary
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_with_low_threshold.inlet(body)

            # Should have fallback note
            has_truncation_note = any(
                "truncated" in m.get("content", "").lower() for m in result["messages"]
            )
            assert has_truncation_note

    @pytest.mark.asyncio
    async def test_timeout_triggers_fallback(self, filter_with_low_threshold):
        """API timeout triggers fallback to truncation."""
        import httpx

        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("Connection timeout")
            )

            result = await filter_with_low_threshold.inlet(body)

            # Should have truncated messages
            assert len(result["messages"]) < len(messages)
            has_truncation_note = any(
                "truncated" in m.get("content", "").lower() for m in result["messages"]
            )
            assert has_truncation_note

    @pytest.mark.asyncio
    async def test_fallback_preserves_system_and_recent(
        self, filter_with_low_threshold
    ):
        """Fallback preserves system prompt and recent messages."""
        messages = (
            [
                {"role": "system", "content": "System prompt"},
            ]
            + [{"role": "user", "content": f"Old message {i} " * 30} for i in range(5)]
            + [
                {"role": "user", "content": "Recent user"},
                {"role": "assistant", "content": "Recent assistant"},
            ]
        )
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("API Error")
            )

            result = await filter_with_low_threshold.inlet(body)

            # System prompt should be first
            assert result["messages"][0]["content"] == "System prompt"
            # Recent messages should be at end
            assert result["messages"][-1]["content"] == "Recent assistant"
            assert result["messages"][-2]["content"] == "Recent user"

    @pytest.mark.asyncio
    async def test_fallback_emits_status_events(self, filter_with_low_threshold):
        """Fallback emits appropriate status events."""
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}
        event_emitter = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("API Error")
            )

            await filter_with_low_threshold.inlet(body, __event_emitter__=event_emitter)

            # Should have multiple status events
            assert event_emitter.call_count >= 2

            # Check that final status mentions truncation/failure
            final_call = event_emitter.call_args_list[-1]
            final_status = final_call[0][0]["data"]["description"]
            assert "truncat" in final_status.lower() or "fail" in final_status.lower()


class TestDebugLogging:
    """Test debug logging functionality."""

    def test_debug_logging_enabled_by_default(self, filter_instance):
        """Debug logging is enabled by default."""
        assert filter_instance.valves.debug_logging is True

    def test_debug_logging_can_be_disabled(self):
        """Debug logging can be disabled via valve."""
        f = Filter()
        f.valves.debug_logging = False
        assert f.valves.debug_logging is False

    def test_format_messages_summary_empty(self, filter_instance):
        """Format empty message list."""
        result = filter_instance._format_messages_summary([])
        assert "empty" in result.lower()

    def test_format_messages_summary_basic(self, filter_instance):
        """Format basic message list."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = filter_instance._format_messages_summary(messages, "Test")
        assert "Test: 2 messages" in result
        assert "user:" in result
        assert "assistant:" in result
        assert "tokens" in result

    def test_format_messages_summary_truncates_long_content(self, filter_instance):
        """Long content is truncated in summary."""
        messages = [
            {"role": "user", "content": "x" * 200},
        ]
        result = filter_instance._format_messages_summary(messages)
        assert "..." in result
        assert len(result) < 300  # Should be truncated

    def test_format_messages_summary_handles_multimodal(self, filter_instance):
        """Handles multimodal content gracefully."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Look"}]},
        ]
        result = filter_instance._format_messages_summary(messages)
        assert "[list]" in result.lower() or "messages" in result

    @pytest.mark.asyncio
    async def test_debug_output_when_enabled(self, filter_with_low_threshold, capsys):
        """Debug output is printed when enabled."""
        filter_with_low_threshold.valves.debug_logging = True
        messages = [{"role": "user", "content": "Hi"}]
        body = {"messages": messages, "model": "test-model"}

        await filter_with_low_threshold.inlet(body)

        captured = capsys.readouterr()
        assert "[ContextSummarization]" in captured.out

    @pytest.mark.asyncio
    async def test_no_debug_output_when_disabled(
        self, filter_with_low_threshold, capsys
    ):
        """No debug output when disabled."""
        filter_with_low_threshold.valves.debug_logging = False
        messages = [{"role": "user", "content": "Hi"}]
        body = {"messages": messages, "model": "test-model"}

        await filter_with_low_threshold.inlet(body)

        captured = capsys.readouterr()
        assert "[ContextSummarization]" not in captured.out


class TestToolSummariesInOutput:
    """Test that tool summaries are included in the summary output."""

    @pytest.mark.asyncio
    async def test_tool_summaries_appended_to_summary(self, filter_with_low_threshold):
        """Tool summaries are appended after the LLM-generated summary."""
        # Create messages with a large tool result
        large_result = (
            "["
            + ",".join(
                [
                    '{"patient": "P' + str(i) + '", "count": ' + str(i) + "}"
                    for i in range(50)
                ]
            )
            + "]"
        )
        messages = [
            {"role": "user", "content": "Query the database " * 20},
            {"role": "assistant", "content": large_result},
            {"role": "user", "content": "Another question " * 20},
            {"role": "assistant", "content": "Here is more info " * 20},
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "response": "Summary of the conversation."
            }
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_with_low_threshold.inlet(body)

            # Find the summary message
            summary_msg = None
            for msg in result["messages"]:
                if "[Previous conversation summary]" in msg.get("content", ""):
                    summary_msg = msg
                    break

            assert summary_msg is not None
            content = summary_msg["content"]
            # Should have the LLM summary
            assert "Summary of the conversation" in content
            # Should have tool summaries section
            assert "[Tool calls from earlier in conversation]" in content
            # Should have the actual tool summary
            assert "50 rows" in content


class TestMessageReconstruction:
    """Test that messages are reconstructed correctly after summarization."""

    @pytest.mark.asyncio
    async def test_message_order_preserved(self, filter_with_low_threshold):
        """Message order is preserved after summarization."""
        messages = (
            [
                {"role": "system", "content": "System prompt"},
            ]
            + [{"role": "user", "content": f"User {i} " * 30} for i in range(5)]
            + [
                {"role": "user", "content": "Final user message"},
                {"role": "assistant", "content": "Final assistant response"},
            ]
        )
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "Summary of conversation"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_with_low_threshold.inlet(body)

            # Check order: system, summary, recent messages
            assert result["messages"][0]["role"] == "system"
            assert result["messages"][0]["content"] == "System prompt"
            assert "[Previous conversation summary]" in result["messages"][1]["content"]
            assert result["messages"][-1]["content"] == "Final assistant response"

    @pytest.mark.asyncio
    async def test_summary_format(self, filter_with_low_threshold):
        """Summary message has correct format."""
        messages = [
            {"role": "user", "content": f"Message {i} " * 30} for i in range(10)
        ]
        body = {"messages": messages, "model": "test-model"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "This is the summary."}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await filter_with_low_threshold.inlet(body)

            # Find summary message
            summary_msg = None
            for msg in result["messages"]:
                if "[Previous conversation summary]" in msg.get("content", ""):
                    summary_msg = msg
                    break

            assert summary_msg is not None
            assert summary_msg["role"] == "system"
            assert "This is the summary." in summary_msg["content"]
            assert "[End of summary" in summary_msg["content"]

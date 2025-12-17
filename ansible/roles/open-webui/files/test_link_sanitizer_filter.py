"""
Unit tests for link_sanitizer_filter.

Run with: pytest ansible/roles/open-webui/files/test_link_sanitizer_filter.py -v
"""

import pytest
from link_sanitizer_filter import Filter


@pytest.fixture
def filter_instance():
    """Create a fresh filter instance for each test."""
    return Filter()


@pytest.fixture
def filter_with_internal_domains():
    """Filter configured with internal domains."""
    f = Filter()
    f.valves.internal_domains = "scout.example.com,keycloak.example.com"
    return f


class TestIsExternal:
    """Test URL external/internal classification."""

    def test_empty_internal_domains_all_external(self, filter_instance):
        """With no internal domains configured, all URLs are external."""
        assert filter_instance.is_external("https://example.com") is True
        assert filter_instance.is_external("https://google.com") is True
        assert filter_instance.is_external("http://localhost") is True

    def test_exact_domain_match(self, filter_with_internal_domains):
        """Exact domain matches are internal."""
        assert (
            filter_with_internal_domains.is_external("https://scout.example.com")
            is False
        )
        assert (
            filter_with_internal_domains.is_external("https://keycloak.example.com")
            is False
        )

    def test_subdomain_match(self, filter_with_internal_domains):
        """Subdomains of internal domains are internal."""
        assert (
            filter_with_internal_domains.is_external("https://api.scout.example.com")
            is False
        )
        assert (
            filter_with_internal_domains.is_external(
                "https://auth.keycloak.example.com"
            )
            is False
        )

    def test_external_domains(self, filter_with_internal_domains):
        """Non-matching domains are external."""
        assert filter_with_internal_domains.is_external("https://evil.com") is True
        assert filter_with_internal_domains.is_external("https://google.com") is True
        assert (
            filter_with_internal_domains.is_external("https://example.com") is True
        )  # Not scout.example.com

    def test_partial_domain_not_match(self, filter_with_internal_domains):
        """Partial domain matches should NOT be considered internal."""
        # notscout.example.com should NOT match scout.example.com
        assert (
            filter_with_internal_domains.is_external("https://notscout.example.com")
            is True
        )
        # example.com.evil.com should NOT match
        assert (
            filter_with_internal_domains.is_external(
                "https://scout.example.com.evil.com"
            )
            is True
        )

    def test_url_with_port(self, filter_with_internal_domains):
        """URLs with ports should still match."""
        assert (
            filter_with_internal_domains.is_external("https://scout.example.com:8080")
            is False
        )
        assert (
            filter_with_internal_domains.is_external(
                "http://scout.example.com:3000/path"
            )
            is False
        )

    def test_url_with_path_and_query(self, filter_with_internal_domains):
        """URLs with paths and query strings should match on domain."""
        assert (
            filter_with_internal_domains.is_external(
                "https://scout.example.com/api/data?id=123"
            )
            is False
        )
        assert (
            filter_with_internal_domains.is_external("https://evil.com/api/data?id=123")
            is True
        )

    def test_case_insensitive(self, filter_with_internal_domains):
        """Domain matching should be case-insensitive."""
        assert (
            filter_with_internal_domains.is_external("https://SCOUT.EXAMPLE.COM")
            is False
        )
        assert (
            filter_with_internal_domains.is_external("https://Scout.Example.Com")
            is False
        )

    def test_invalid_url_treated_as_external(self, filter_instance):
        """Malformed URLs should be treated as external (safe default)."""
        assert filter_instance.is_external("not-a-url") is True
        assert filter_instance.is_external("") is True
        assert filter_instance.is_external("://missing-scheme") is True

    def test_userinfo_and_auth_bypass(self, filter_with_internal_domains):
        """URLs with userinfo should extract host correctly and not be spoofable."""
        # Normal auth - internal domain is host
        assert (
            filter_with_internal_domains.is_external(
                "https://user:pass@scout.example.com/"
            )
            is False
        )
        # Bypass attempt - internal domain is in userinfo, evil.com is host
        assert (
            filter_with_internal_domains.is_external(
                "https://scout.example.com@evil.com/"
            )
            is True
        )

    def test_ipv4_urls_external(self, filter_instance):
        """IPv4 address URLs are treated as external."""
        assert filter_instance.is_external("http://192.168.1.1") is True
        assert filter_instance.is_external("https://10.0.0.1/path") is True
        assert filter_instance.is_external("http://8.8.8.8:8080/api") is True

    def test_ipv4_urls_with_internal_domains(self, filter_with_internal_domains):
        """IPv4 URLs are external even when internal domains are configured."""
        # IP addresses don't match domain names
        assert filter_with_internal_domains.is_external("http://192.168.1.1") is True
        assert filter_with_internal_domains.is_external("https://10.0.0.1") is True

    def test_ipv6_urls_external(self, filter_instance):
        """IPv6 address URLs are treated as external."""
        assert filter_instance.is_external("http://[::1]") is True
        assert filter_instance.is_external("http://[2001:db8::1]") is True
        assert filter_instance.is_external("http://[fe80::1]:8080/path") is True

    def test_localhost_external(self, filter_instance):
        """localhost URLs are treated as external (no internal domains configured)."""
        assert filter_instance.is_external("http://localhost") is True
        assert filter_instance.is_external("http://localhost:3000") is True
        assert filter_instance.is_external("http://127.0.0.1") is True


class TestSanitizeContent:
    """Test content sanitization with various URL formats."""

    def test_markdown_link_external(self, filter_instance):
        """External markdown links are sanitized."""
        content = "Check out [this resource](https://evil.com/data)"
        result = filter_instance.sanitize_content(content)
        assert "https://evil.com" not in result
        assert "this resource" in result
        assert "(external link removed for security)" in result

    def test_markdown_link_internal(self, filter_with_internal_domains):
        """Internal markdown links are preserved."""
        content = "See [documentation](https://scout.example.com/docs)"
        result = filter_with_internal_domains.sanitize_content(content)
        assert result == content

    def test_markdown_link_empty_text(self, filter_instance):
        """Markdown links with empty text are handled."""
        content = "Click [](https://evil.com/exfil?data=secret)"
        result = filter_instance.sanitize_content(content)
        assert "https://evil.com" not in result
        assert "(external link removed for security)" in result

    def test_raw_url_external(self, filter_instance):
        """Raw external URLs are sanitized."""
        content = "Visit https://evil.com/exfil?patient=12345 for more info"
        result = filter_instance.sanitize_content(content)
        assert "https://evil.com" not in result
        assert "(external link removed for security)" in result

    def test_raw_url_internal(self, filter_with_internal_domains):
        """Raw internal URLs are preserved."""
        content = "API endpoint: https://scout.example.com/api/v1"
        result = filter_with_internal_domains.sanitize_content(content)
        assert result == content

    def test_multiple_urls_mixed(self, filter_with_internal_domains):
        """Mix of internal and external URLs are handled correctly."""
        content = """
        Internal: https://scout.example.com/api
        External: https://quickchart.io/chart?data=sensitive
        Link: [docs](https://scout.example.com/docs)
        Bad: [chart](https://evil.com/exfil)
        """
        result = filter_with_internal_domains.sanitize_content(content)
        assert "https://scout.example.com/api" in result
        assert "https://scout.example.com/docs" in result
        assert "https://quickchart.io" not in result
        assert "https://evil.com" not in result

    def test_no_urls_unchanged(self, filter_instance):
        """Content without URLs is unchanged."""
        content = "This is plain text without any URLs."
        result = filter_instance.sanitize_content(content)
        assert result == content

    def test_http_and_https(self, filter_instance):
        """Both http and https schemes are handled."""
        content = "http://evil.com and https://evil.com"
        result = filter_instance.sanitize_content(content)
        assert "http://evil.com" not in result
        assert "https://evil.com" not in result

    def test_www_urls_sanitized(self, filter_instance):
        """URLs starting with www. are sanitized."""
        content = "Visit www.evil.com/exfil?data=secret for more"
        result = filter_instance.sanitize_content(content)
        assert "www.evil.com" not in result
        assert "(external link removed for security)" in result

    def test_www_markdown_link_sanitized(self, filter_instance):
        """Markdown links with www. URLs are sanitized."""
        content = "[Click here](www.evil.com/steal?id=123)"
        result = filter_instance.sanitize_content(content)
        assert "www.evil.com" not in result
        assert "Click here" in result

    def test_www_internal_domain_preserved(self, filter_with_internal_domains):
        """www. URLs for internal domains are preserved."""
        content = "Visit www.scout.example.com/docs for help"
        result = filter_with_internal_domains.sanitize_content(content)
        assert "www.scout.example.com/docs" in result

    def test_ipv4_url_sanitized(self, filter_instance):
        """IPv4 address URLs are sanitized."""
        content = "Connect to http://192.168.1.1:8080/api for the service"
        result = filter_instance.sanitize_content(content)
        assert "192.168.1.1" not in result
        assert "(external link removed for security)" in result

    def test_ipv6_url_sanitized(self, filter_instance):
        """IPv6 address URLs are sanitized."""
        content = "The server is at http://[2001:db8::1]/endpoint"
        result = filter_instance.sanitize_content(content)
        assert "2001:db8" not in result
        assert "(external link removed for security)" in result

    def test_localhost_url_sanitized(self, filter_instance):
        """localhost URLs are sanitized (when no internal domains configured)."""
        content = "Debug at http://localhost:3000/debug"
        result = filter_instance.sanitize_content(content)
        assert "localhost" not in result
        assert "(external link removed for security)" in result

    def test_url_in_double_quotes_sanitized(self, filter_instance):
        """URLs in double quotes are still sanitized."""
        content = 'The URL is "https://evil.com/data" in the config'
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result
        assert "(external link removed for security)" in result

    def test_url_in_single_quotes_sanitized(self, filter_instance):
        """URLs in single quotes are still sanitized."""
        content = "Set the endpoint to 'https://evil.com/api' in settings"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result
        assert "(external link removed for security)" in result


class TestDataExfiltrationPatterns:
    """
    Test known data exfiltration attack patterns.
    These serve as regression tests for security-critical URL handling.
    """

    def test_quickchart_exfiltration(self, filter_instance):
        """QuickChart-style data exfiltration with special chars in query."""
        content = "![chart](https://quickchart.io/chart?c={type:'bar',data:{labels:['Patient_A']}})"
        result = filter_instance.sanitize_content(content)
        assert "quickchart.io" not in result

    def test_url_encoded_data(self, filter_instance):
        """URL-encoded sensitive data in query parameters."""
        content = (
            "[Results](https://attacker.com/log?data=MRN%3D12345%26diagnosis%3Dcancer)"
        )
        result = filter_instance.sanitize_content(content)
        assert "attacker.com" not in result

    def test_base64_in_url(self, filter_instance):
        """Base64-encoded data (contains = signs) in URLs."""
        content = "https://evil.com/exfil?d=SGVsbG8gV29ybGQ="
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result

    def test_misleading_link_text_preserved(self, filter_instance):
        """Link text is preserved even when URL is removed."""
        content = "[Click here for secure results](https://evil.com/steal?id=123)"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result
        assert "Click here for secure results" in result


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_url_at_end_of_sentence(self, filter_instance):
        """URLs at end of sentences are handled."""
        content = "Visit https://evil.com."
        result = filter_instance.sanitize_content(content)
        # Should remove URL but keep period
        assert "evil.com" not in result

    def test_url_in_parentheses(self, filter_instance):
        """URLs in parentheses are handled."""
        content = "Source: (https://evil.com/source)"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result

    def test_url_in_brackets(self, filter_instance):
        """URLs in square brackets (but not markdown) are handled."""
        content = "Reference [1]: https://evil.com/ref"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result

    def test_multiple_markdown_links_same_line(self, filter_instance):
        """Multiple markdown links on same line are all processed."""
        content = "[Link1](https://a.com) and [Link2](https://b.com)"
        result = filter_instance.sanitize_content(content)
        assert "https://a.com" not in result
        assert "https://b.com" not in result
        assert "Link1" in result
        assert "Link2" in result

    def test_empty_content(self, filter_instance):
        """Empty content is handled."""
        assert filter_instance.sanitize_content("") == ""

    def test_whitespace_only(self, filter_instance):
        """Whitespace-only content is unchanged."""
        content = "   \n\t  "
        assert filter_instance.sanitize_content(content) == content

    def test_unicode_in_url(self, filter_instance):
        """URLs with unicode characters are handled."""
        content = "https://evil.com/path?name=日本語"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result

    def test_very_long_url(self, filter_instance):
        """Very long URLs are handled."""
        long_data = "a" * 1000
        content = f"https://evil.com/exfil?data={long_data}"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result

    def test_url_with_fragment(self, filter_instance):
        """URLs with fragments are handled."""
        content = "https://evil.com/page#section"
        result = filter_instance.sanitize_content(content)
        assert "evil.com" not in result


class TestValvesConfiguration:
    """Test filter behavior with different valve configurations."""

    def test_custom_replacement_text(self):
        """Custom replacement text is used."""
        f = Filter()
        f.valves.replacement_text = "[BLOCKED]"
        content = "https://evil.com/data"
        result = f.sanitize_content(content)
        assert "[BLOCKED]" in result
        assert "(external link removed for security)" not in result

    def test_internal_domains_whitespace(self):
        """Internal domains with extra whitespace are handled."""
        f = Filter()
        f.valves.internal_domains = " scout.example.com , keycloak.example.com "
        assert f.is_external("https://scout.example.com") is False
        assert f.is_external("https://keycloak.example.com") is False

    def test_internal_domains_empty_entries(self):
        """Empty entries in internal domains are ignored."""
        f = Filter()
        f.valves.internal_domains = "scout.example.com,,keycloak.example.com,"
        domains = f.get_internal_domains()
        assert "" not in domains
        assert len(domains) == 2


class TestOutlet:
    """Test the outlet async method."""

    def test_outlet_sanitizes_assistant_not_user(self):
        """Outlet sanitizes assistant messages but preserves user messages."""
        import asyncio

        async def run():
            f = Filter()
            body = {
                "messages": [
                    {"role": "user", "content": "https://user-provided.com"},
                    {"role": "assistant", "content": "Check https://evil.com/data"},
                ]
            }
            return await f.outlet(body)

        result = asyncio.run(run())
        # User message unchanged
        assert result["messages"][0]["content"] == "https://user-provided.com"
        # Assistant message sanitized
        assert "evil.com" not in result["messages"][1]["content"]

    def test_outlet_empty_messages(self):
        """Outlet handles empty messages array."""
        import asyncio

        async def run():
            f = Filter()
            body = {"messages": []}
            return await f.outlet(body)

        result = asyncio.run(run())
        assert result == {"messages": []}

    def test_outlet_no_messages_key(self):
        """Outlet handles missing messages key."""
        import asyncio

        async def run():
            f = Filter()
            body = {}
            return await f.outlet(body)

        result = asyncio.run(run())
        assert result == {}

    def test_outlet_calls_event_emitter_on_change(self):
        """Outlet calls event emitter when content changes."""
        import asyncio

        emitted = []

        async def run():
            f = Filter()

            async def mock_emitter(event):
                emitted.append(event)

            body = {
                "messages": [{"role": "assistant", "content": "https://evil.com/data"}]
            }
            return await f.outlet(body, __event_emitter__=mock_emitter)

        asyncio.run(run())

        assert len(emitted) == 1
        assert emitted[0]["type"] == "replace"
        assert "evil.com" not in emitted[0]["data"]["content"]

    def test_outlet_no_emitter_call_when_unchanged(self):
        """Outlet doesn't call emitter when content unchanged."""
        import asyncio

        emitted = []

        async def run():
            f = Filter()
            f.valves.internal_domains = "example.com"

            async def mock_emitter(event):
                emitted.append(event)

            body = {
                "messages": [
                    {"role": "assistant", "content": "Visit https://example.com"}
                ]
            }
            return await f.outlet(body, __event_emitter__=mock_emitter)

        asyncio.run(run())

        assert len(emitted) == 0


class TestStreamProcessing:
    """Test real-time stream processing."""

    def test_stream_no_url(self):
        """Stream without URLs passes through."""
        f = Filter()
        event = {"choices": [{"delta": {"content": "Hello world"}}]}
        metadata = {"chat_id": "test-chat-1"}
        result = f.stream(event, __metadata__=metadata)
        # Buffer holds last 7 chars, rest emitted
        assert result["choices"][0]["delta"]["content"] == "Hell"

    def test_stream_url_in_single_chunk(self):
        """URL contained in single chunk is sanitized."""
        f = Filter()
        event = {"choices": [{"delta": {"content": "Visit https://evil.com for info"}}]}
        metadata = {"chat_id": "test-chat-1"}
        result = f.stream(event, __metadata__=metadata)
        content = result["choices"][0]["delta"]["content"]
        assert "evil.com" not in content
        assert "(external link removed for security)" in content

    def test_stream_url_split_across_chunks(self):
        """URL split across chunks is properly buffered and sanitized."""
        f = Filter()
        metadata = {"chat_id": "test-chat-1"}

        # First chunk: partial URL
        event1 = {"choices": [{"delta": {"content": "Check https://ev"}}]}
        result1 = f.stream(event1, __metadata__=metadata)
        # Should emit "Check " and buffer the partial URL
        assert "evil.com" not in result1["choices"][0]["delta"]["content"]

        # Second chunk: rest of URL (same chat_id)
        event2 = {"choices": [{"delta": {"content": "il.com/data now"}}]}
        result2 = f.stream(event2, __metadata__=metadata)
        content2 = result2["choices"][0]["delta"]["content"]
        assert "evil.com" not in content2
        assert "(external link removed for security)" in content2

    def test_stream_internal_url_preserved(self):
        """Internal URLs are preserved in stream."""
        f = Filter()
        f.valves.internal_domains = "example.com"
        metadata = {"chat_id": "test-chat-1"}

        event = {"choices": [{"delta": {"content": "See https://example.com/docs "}}]}
        result = f.stream(event, __metadata__=metadata)
        content = result["choices"][0]["delta"]["content"]
        assert "https://example.com/docs" in content

    def test_stream_finish_flushes_buffer(self):
        """End of stream flushes remaining buffer."""
        f = Filter()
        metadata = {"chat_id": "test-chat-1"}

        # Send content without URL
        event1 = {"choices": [{"delta": {"content": "Short"}}]}
        result1 = f.stream(event1, __metadata__=metadata)
        # Buffer too short, nothing emitted
        assert result1["choices"][0]["delta"]["content"] == ""

        # Finish signal (same chat_id)
        event2 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        result2 = f.stream(event2, __metadata__=metadata)
        assert result2["choices"][0]["delta"]["content"] == "Short"

    def test_stream_multiple_urls(self):
        """Multiple URLs in stream are all processed."""
        f = Filter()
        metadata = {"chat_id": "test-chat-1"}
        event = {
            "choices": [
                {
                    "delta": {
                        "content": "Bad: https://a.com Good later https://b.com end "
                    }
                }
            ],
        }
        result = f.stream(event, __metadata__=metadata)
        content = result["choices"][0]["delta"]["content"]
        assert "https://a.com" not in content
        assert "https://b.com" not in content
        assert content.count("(external link removed for security)") == 2

    def test_stream_http_split_at_boundary(self):
        """'http' split across chunks doesn't cause issues."""
        f = Filter()
        metadata = {"chat_id": "test-chat-1"}

        # First chunk ends with 'htt'
        event1 = {"choices": [{"delta": {"content": "Link: htt"}}]}
        result1 = f.stream(event1, __metadata__=metadata)

        # Second chunk completes the URL (same chat_id)
        event2 = {"choices": [{"delta": {"content": "ps://evil.com here "}}]}
        result2 = f.stream(event2, __metadata__=metadata)

        # Combined should have replaced the URL
        combined = (
            result1["choices"][0]["delta"]["content"]
            + result2["choices"][0]["delta"]["content"]
        )
        assert "evil.com" not in combined

    def test_stream_concurrent_isolation(self):
        """
        Multiple concurrent streams are isolated from each other.

        This is critical for thread-safety: Open WebUI reuses a single Filter
        instance across all users, so buffers must be keyed by chat_id to
        prevent data leakage between users.
        """
        f = Filter()
        metadata_a = {"chat_id": "chat-user-a"}
        metadata_b = {"chat_id": "chat-user-b"}

        # User A starts streaming (contains PHI)
        event_a1 = {"choices": [{"delta": {"content": "Patient MRN 12345 "}}]}
        result_a1 = f.stream(event_a1, __metadata__=metadata_a)

        # User B starts streaming concurrently (different chat_id)
        event_b1 = {"choices": [{"delta": {"content": "Hello, how can I "}}]}
        result_b1 = f.stream(event_b1, __metadata__=metadata_b)

        # User A continues
        event_a2 = {"choices": [{"delta": {"content": "has diagnosis "}}]}
        result_a2 = f.stream(event_a2, __metadata__=metadata_a)

        # User B continues
        event_b2 = {"choices": [{"delta": {"content": "help you today?"}}]}
        result_b2 = f.stream(event_b2, __metadata__=metadata_b)

        # User A finishes
        event_a3 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        result_a3 = f.stream(event_a3, __metadata__=metadata_a)

        # User B finishes
        event_b3 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        result_b3 = f.stream(event_b3, __metadata__=metadata_b)

        # Reconstruct User A's full output
        user_a_output = (
            result_a1["choices"][0]["delta"]["content"]
            + result_a2["choices"][0]["delta"]["content"]
            + result_a3["choices"][0]["delta"]["content"]
        )

        # Reconstruct User B's full output
        user_b_output = (
            result_b1["choices"][0]["delta"]["content"]
            + result_b2["choices"][0]["delta"]["content"]
            + result_b3["choices"][0]["delta"]["content"]
        )

        # User A's output should contain their content, not User B's
        assert "Patient" in user_a_output or "MRN" in user_a_output
        assert "how can I" not in user_a_output

        # User B's output should contain their content, not User A's
        assert "how can I" in user_b_output or "help you" in user_b_output
        assert "Patient" not in user_b_output
        assert "MRN" not in user_b_output

        # Buffers should be cleaned up after finish
        assert "chat-user-a" not in f._stream_buffers
        assert "chat-user-b" not in f._stream_buffers

    def test_stream_buffer_cleanup_on_finish(self):
        """Stream buffer is cleaned up when finish_reason is received."""
        f = Filter()
        metadata = {"chat_id": "test-cleanup"}

        # Start a stream
        event1 = {"choices": [{"delta": {"content": "Hello"}}]}
        f.stream(event1, __metadata__=metadata)
        assert "test-cleanup" in f._stream_buffers

        # Finish the stream
        event2 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        f.stream(event2, __metadata__=metadata)

        # Buffer should be cleaned up
        assert "test-cleanup" not in f._stream_buffers

    def test_stream_stale_buffer_cleanup(self):
        """Stale buffers from abandoned streams are cleaned up."""
        import time

        f = Filter()
        metadata = {"chat_id": "new-chat"}

        # Manually insert an "old" buffer entry with expired timestamp
        old_timestamp = time.time() - f.STREAM_BUFFER_TTL - 100  # Expired
        f._stream_buffers["abandoned-stream"] = ("partial content", old_timestamp)

        # Insert a "fresh" buffer entry
        fresh_timestamp = time.time()
        f._stream_buffers["active-stream"] = ("fresh content", fresh_timestamp)

        # Process a new stream event (triggers cleanup)
        event = {"choices": [{"delta": {"content": "Hello"}}]}
        f.stream(event, __metadata__=metadata)

        # Stale buffer should be cleaned up
        assert "abandoned-stream" not in f._stream_buffers

        # Fresh buffer should still exist
        assert "active-stream" in f._stream_buffers

        # New stream buffer should exist
        assert "new-chat" in f._stream_buffers

    def test_stream_without_metadata_uses_default(self):
        """Stream without metadata falls back to default buffer."""
        f = Filter()

        # No metadata provided
        event1 = {"choices": [{"delta": {"content": "Hello world "}}]}
        result1 = f.stream(event1)
        assert result1["choices"][0]["delta"]["content"] == "Hello"

        # Finish without metadata
        event2 = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        result2 = f.stream(event2)
        # Buffer kept last 7 chars: " world " (with leading space)
        assert result2["choices"][0]["delta"]["content"] == " world "

    def test_stream_www_url_sanitized(self):
        """www. URLs in stream are sanitized."""
        f = Filter()
        metadata = {"chat_id": "test-chat-1"}
        event = {
            "choices": [{"delta": {"content": "Visit www.evil.com/data for info "}}]
        }
        result = f.stream(event, __metadata__=metadata)
        content = result["choices"][0]["delta"]["content"]
        assert "www.evil.com" not in content
        assert "(external link removed for security)" in content


class TestPreservedContent:
    """Test that certain content patterns are NOT modified."""

    def test_relative_urls_not_modified(self, filter_instance):
        """Relative URLs should not be modified."""
        content = "See [docs](/path/to/docs) for more."
        result = filter_instance.sanitize_content(content)
        assert result == content

    def test_anchor_links_not_modified(self, filter_instance):
        """Anchor links should not be modified."""
        content = "Jump to [section](#my-section)"
        result = filter_instance.sanitize_content(content)
        assert result == content

    def test_email_not_modified(self, filter_instance):
        """Email addresses should not be modified."""
        content = "Contact support@example.com for help."
        result = filter_instance.sanitize_content(content)
        assert result == content

    def test_code_blocks_urls(self, filter_instance):
        """URLs that look like code examples."""
        # Note: The filter doesn't detect code blocks, so URLs in them
        # will still be sanitized. This documents current behavior.
        content = "```\ncurl https://api.example.com/endpoint\n```"
        result = filter_instance.sanitize_content(content)
        # Current behavior: URL is sanitized even in code block
        assert "api.example.com" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

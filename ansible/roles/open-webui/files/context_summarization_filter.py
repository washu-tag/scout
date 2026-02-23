"""
title: Context Summarization Filter
description: Summarizes older conversation history when approaching context limits.
             Preserves base system prompt, compacts large tool results embedded in
             assistant messages, and lets RAG re-retrieve fresh knowledge.
author: Scout Team
version: 2.1.0

Note on message format (verified via debug filter 2024-12):
    Open WebUI inlet receives messages in a simplified format:
    - Only user/assistant/system roles (no role:"tool" messages)
    - Tool results are embedded directly in assistant message content
    - Tool results appear as escaped JSON with HTML entities (&quot; etc.)
    - No tool_calls array in assistant messages
"""

import html
import json
import re
import traceback
import tiktoken
import httpx
from typing import Optional, Callable, Any, Awaitable, Tuple
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        token_threshold: int = Field(
            default=100000,
            description="Token count that triggers summarization (default ~77% of 128K)",
        )
        messages_to_keep: int = Field(
            default=10,
            description="Number of recent messages to preserve",
        )
        min_messages_to_keep: int = Field(
            default=2,
            description="Minimum messages to keep even when dynamically reducing",
        )
        ollama_url: str = Field(
            default="http://ollama:11434",
            description="Ollama API URL for summarization calls",
        )
        summarizer_model: str = Field(
            default="",
            description="Model for summarization (empty = use chat model)",
        )
        tool_result_token_threshold: int = Field(
            default=500,
            description="Compact tool results in assistant messages exceeding this token count",
        )
        debug_logging: bool = Field(
            default=False,
            description="Enable detailed debug logging (visible in container logs)",
        )
        dump_full_messages: bool = Field(
            default=False,
            description="Dump full raw message content (verbose - for debugging message format)",
        )
        priority: int = Field(default=0)

    def __init__(self):
        self.valves = self.Valves()
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def _debug(self, message: str) -> None:
        """Print debug message if debug logging is enabled."""
        if self.valves.debug_logging:
            print(f"[ContextSummarization] {message}")

    def _format_messages_summary(self, messages: list, label: str = "Messages") -> str:
        """Format a summary of messages for debug logging."""
        if not messages:
            return f"{label}: (empty)"

        lines = [f"{label}: {len(messages)} messages"]
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            tokens = self.count_message_tokens(msg)

            if isinstance(content, str):
                if self.valves.dump_full_messages:
                    # Full content - no truncation
                    lines.append(f"  [{i}] {role}: {tokens} tokens")
                    lines.append(f"--- START CONTENT ---")
                    lines.append(content)
                    lines.append(f"--- END CONTENT ---")
                else:
                    # Truncated preview
                    preview = content[:80].replace("\n", " ")
                    if len(content) > 80:
                        preview += "..."
                    lines.append(f"  [{i}] {role}: {tokens} tokens - {preview}")
            else:
                lines.append(
                    f"  [{i}] {role}: {tokens} tokens - [{type(content).__name__}]"
                )
        return "\n".join(lines)

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string using tiktoken."""
        if not text:
            return 0
        return len(self.encoding.encode(text))

    def count_message_tokens(self, msg: dict) -> int:
        """Count tokens in a message."""
        content = msg.get("content", "")
        if isinstance(content, str):
            return self.count_tokens(content)
        elif isinstance(content, list):
            # Multimodal content
            total = 0
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    total += self.count_tokens(item.get("text", ""))
            return total
        return 0

    def count_all_tokens(self, messages: list) -> int:
        """Count total tokens across all messages."""
        return sum(self.count_message_tokens(msg) for msg in messages)

    def extract_base_system_prompt(self, messages: list) -> Tuple[list, list]:
        """
        Extract the base system prompt (first system message only).

        Subsequent system messages may contain RAG/knowledge content that is
        re-retrieved per-query, so we don't preserve those - let RAG refresh.

        Returns: (base_system_messages, remaining_messages)
        """
        if not messages:
            return [], []

        # Only preserve the FIRST system message (the base model prompt)
        if messages[0].get("role") == "system":
            return [messages[0]], messages[1:]

        return [], messages

    def has_embedded_tool_result(self, content: str) -> bool:
        """
        Detect if assistant message content contains embedded tool results.

        Tool results in Open WebUI appear as escaped JSON with HTML entities,
        typically containing patterns like "results" or structured data.
        """
        if not content:
            return False

        # Look for patterns indicating embedded tool results:
        # 1. HTML-escaped JSON: &quot;results&quot; or &quot;error&quot;
        # 2. Double-escaped JSON: \"results\" or \\&quot;
        # 3. Raw JSON patterns at start: {"results" or [{"
        patterns = [
            r"&quot;results&quot;",
            r"&quot;error&quot;",
            r"\\&quot;results\\&quot;",
            r'\\"results\\"',
            r'^[\s\n]*\{["\']results',
            r"^[\s\n]*\[\{",
            r'^[\s\n]*"&quot;',  # Starts with escaped quote (common pattern)
        ]

        for pattern in patterns:
            if re.search(pattern, content[:1000]):  # Check first 1000 chars
                return True

        return False

    def extract_tool_result_info(self, content: str) -> dict:
        """
        Extract information about embedded tool results for compaction.

        Returns dict with:
        - has_results: bool
        - result_count: int (number of items/rows if detectable)
        - sample_row: dict (first row of results, truncated values)
        - has_error: bool
        - error_count: int (number of failed tool calls)
        """
        info = {
            "has_results": False,
            "result_count": None,
            "sample_row": None,
            "has_error": False,
            "error_count": 0,
        }

        if not content:
            return info

        # Decode HTML entities (e.g., &quot; -> ", &#x27; -> ')
        try:
            decoded = html.unescape(content)
            # Also handle escaped quotes and newlines from JSON serialization
            decoded = decoded.replace('\\"', '"').replace("\\n", "\n")
        except Exception:
            decoded = content

        # Count error messages (query execution failed patterns)
        error_matches = re.findall(r"query execution failed", decoded, re.IGNORECASE)
        if error_matches:
            info["has_error"] = True
            info["error_count"] = (
                len(error_matches) // 2
            )  # Usually duplicated in message

        # Try to find and parse the successful result JSON
        # Look for {"results": [...]} pattern
        results_match = re.search(r'\{\s*"results"\s*:\s*\[([\s\S]*?)\]\s*\}', decoded)
        if results_match:
            try:
                json_str = '{"results": [' + results_match.group(1) + "]}"
                json_str = json_str.replace('\\"', '"').replace("\\n", "\n")
                data = json.loads(json_str)

                if "results" in data and isinstance(data["results"], list):
                    results = data["results"]
                    info["has_results"] = True
                    info["result_count"] = len(results)

                    # Get sample row (first row with truncated string values)
                    if results and isinstance(results[0], dict):
                        sample = {}
                        for k, v in results[0].items():
                            if isinstance(v, str) and len(v) > 40:
                                sample[k] = v[:40] + "..."
                            else:
                                sample[k] = v
                        info["sample_row"] = sample
            except json.JSONDecodeError:
                pass

        # Fallback: try to find raw JSON array
        if not info["has_results"]:
            array_match = re.search(r"\[\s*\{[\s\S]*?\}\s*\]", decoded)
            if array_match:
                try:
                    json_str = (
                        array_match.group(0).replace('\\"', '"').replace("\\n", "\n")
                    )
                    data = json.loads(json_str)
                    if isinstance(data, list) and data:
                        info["has_results"] = True
                        info["result_count"] = len(data)
                        if isinstance(data[0], dict):
                            sample = {}
                            for k, v in data[0].items():
                                if isinstance(v, str) and len(v) > 40:
                                    sample[k] = v[:40] + "..."
                                else:
                                    sample[k] = v
                            info["sample_row"] = sample
                except json.JSONDecodeError:
                    pass

        # Final fallback: pattern-based detection
        if not info["has_results"] and self.has_embedded_tool_result(content):
            info["has_results"] = True

        return info

    def compact_assistant_with_tool_result(self, msg: dict) -> dict:
        """
        Replace an assistant message containing large tool results with a compacted version.

        Extracts any conversational text and replaces the tool result data with
        a brief description including row count and sample data.
        """
        content = msg.get("content", "")
        if not content:
            return msg

        info = self.extract_tool_result_info(content)

        # Build description of the tool result
        parts = []

        # Add error info if there were failures
        if info["has_error"] and info["error_count"] > 0:
            parts.append(f"{info['error_count']} failed queries")

        # Add result info
        if info["result_count"] is not None:
            parts.append(f"{info['result_count']} rows")
            if info["sample_row"]:
                # Format sample row compactly
                sample_str = json.dumps(info["sample_row"], ensure_ascii=False)
                if len(sample_str) > 120:
                    sample_str = sample_str[:120] + "...}"
                parts.append(sample_str)
        elif info["has_results"]:
            parts.append("results returned")

        if parts:
            result_desc = f"[Tool: {' | '.join(parts)}]"
        else:
            char_count = len(content)
            result_desc = f"[Tool: {char_count:,} chars]"

        # Try to extract any conversational text after the tool results
        # Often the assistant adds commentary/analysis after showing results
        try:
            decoded = html.unescape(content)
            # Look for markdown or plain text after the last JSON block
            # Pattern: find content after the last }" that looks like prose
            last_json_end = max(
                decoded.rfind('}"'),
                decoded.rfind("}'"),
                decoded.rfind('}]"'),
            )
            if last_json_end > 0:
                after_json = decoded[last_json_end + 2 :].strip()
                # Check if there's meaningful commentary (not just whitespace or quotes)
                cleaned = re.sub(r'^[\s"\'\n]+', "", after_json)
                if len(cleaned) > 30:
                    # Truncate if too long
                    if len(cleaned) > 500:
                        cleaned = cleaned[:500] + "..."
                    return {
                        "role": "assistant",
                        "content": f"{result_desc}\n\n{cleaned}",
                    }
        except Exception:
            pass

        return {"role": "assistant", "content": result_desc}

    def split_conversation(self, messages: list, keep_count: int) -> Tuple[list, list]:
        """
        Split conversation messages into old (to summarize) and recent (to keep intact).
        """
        if len(messages) <= keep_count:
            return [], messages

        split_idx = len(messages) - keep_count
        return messages[:split_idx], messages[split_idx:]

    def find_dynamic_split(
        self, messages: list, initial_keep: int, min_keep: int = 2
    ) -> Tuple[list, list]:
        """
        Dynamically find a split point that gives us something to summarize.
        Reduces keep_count until we have at least some messages to summarize.

        Args:
            messages: Conversation messages to split
            initial_keep: Starting value for messages to keep
            min_keep: Minimum messages to keep (default 2)

        Returns: (old_messages, recent_messages)
        """
        keep_count = initial_keep

        while keep_count >= min_keep:
            old_messages, recent_messages = self.split_conversation(
                messages, keep_count
            )
            if old_messages:
                return old_messages, recent_messages
            keep_count = keep_count // 2

        # Last resort: keep minimum and summarize rest
        if len(messages) > min_keep:
            return messages[:-min_keep], messages[-min_keep:]

        return [], messages

    def prepare_for_summarization(self, messages: list) -> Tuple[list, list]:
        """
        Prepare old messages for summarization.

        - Compacts assistant messages with large embedded tool results
        - Passes through user messages unchanged
        - Skips system messages (RAG content will be re-retrieved)
        - Collects tool summaries separately for direct inclusion

        Returns: (messages_for_llm_summary, tool_summaries)
        - messages_for_llm_summary: Messages to send to LLM for narrative summary
        - tool_summaries: List of tool result descriptions to append verbatim
        """
        prepared = []
        tool_summaries = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                # Skip system messages - RAG will re-retrieve
                continue
            elif role == "assistant":
                # Check if this has embedded tool results
                if self.has_embedded_tool_result(content):
                    token_count = self.count_tokens(content)
                    if token_count > self.valves.tool_result_token_threshold:
                        # Compact the tool result
                        compacted = self.compact_assistant_with_tool_result(msg)
                        prepared.append(compacted)
                        # Extract just the tool summary line for separate inclusion
                        compacted_content = compacted.get("content", "")
                        tool_match = re.match(r"(\[Tool:[^\]]+\])", compacted_content)
                        if tool_match:
                            tool_summaries.append(tool_match.group(1))
                    else:
                        # Small enough to keep as-is
                        prepared.append(msg)
                else:
                    # Regular assistant message
                    prepared.append(msg)
            elif role == "user":
                prepared.append(msg)

        return prepared, tool_summaries

    def _build_summarization_prompt(self, messages: list) -> str:
        """Build the prompt for summarizing messages."""
        conversation_parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                conversation_parts.append(f"{role}: {content}")

        if not conversation_parts:
            return ""

        conversation = "\n\n".join(conversation_parts)

        return f"""Summarize the following conversation concisely, preserving key facts, decisions, queries made, and context needed to continue the conversation:

{conversation}

Provide a clear, factual summary in 2-3 paragraphs."""

    async def summarize_messages(self, messages: list, model: str) -> str:
        """Call Ollama to summarize user/assistant conversation."""
        prompt = self._build_summarization_prompt(messages)
        if not prompt:
            return ""

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.valves.ollama_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            result = response.json()
            return result.get("response", "")

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Callable[[Any], Awaitable[None]] = None,
    ) -> dict:
        messages = body.get("messages", [])
        if not messages:
            self._debug("No messages in body, passing through")
            return body

        token_count = self.count_all_tokens(messages)
        self._debug(
            f"=== INLET START === Total tokens: {token_count:,}, Threshold: {self.valves.token_threshold:,}"
        )
        self._debug(self._format_messages_summary(messages, "INPUT"))

        # Dump full message content if enabled (for debugging message format)
        if self.valves.dump_full_messages:
            print(
                f"[ContextSummarization] === FULL MESSAGE DUMP ({len(messages)} messages) ==="
            )
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                print(f"[ContextSummarization] --- MESSAGE {i} ({role}) ---")
                print(f"[ContextSummarization] {content}")
                print(f"[ContextSummarization] --- END MESSAGE {i} ---")
            print(f"[ContextSummarization] === END FULL MESSAGE DUMP ===")

        if token_count < self.valves.token_threshold:
            self._debug(
                f"Under threshold ({token_count:,} < {self.valves.token_threshold:,}), passing through"
            )
            return body  # No summarization needed

        self._debug(
            f"TRIGGERED: {token_count:,} tokens >= {self.valves.token_threshold:,} threshold"
        )

        # Show status to user
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Summarizing conversation ({token_count:,} tokens)...",
                        "done": False,
                    },
                }
            )

        # Step 1: Extract base system prompt only (let RAG re-retrieve knowledge)
        system_messages, conversation = self.extract_base_system_prompt(messages)
        self._debug(
            f"Extracted {len(system_messages)} system message(s), {len(conversation)} conversation messages"
        )
        if system_messages:
            system_tokens = self.count_all_tokens(system_messages)
            self._debug(f"System prompt tokens: {system_tokens:,}")

        # Step 2: Split conversation into old and recent (with dynamic adjustment)
        old_messages, recent_messages = self.find_dynamic_split(
            conversation,
            self.valves.messages_to_keep,
            self.valves.min_messages_to_keep,
        )
        self._debug(
            f"Split: {len(old_messages)} old messages, {len(recent_messages)} recent messages"
        )

        if not old_messages:
            # Can't summarize - warn user and pass through
            self._debug(
                "WARNING: No old messages to summarize, passing through unchanged"
            )
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Context too large to summarize effectively",
                            "done": True,
                        },
                    }
                )
            return body

        self._debug(self._format_messages_summary(old_messages, "OLD (to summarize)"))
        self._debug(self._format_messages_summary(recent_messages, "RECENT (to keep)"))

        # Step 3: Prepare old messages for summarization (compacts tool results)
        prepared_messages, tool_summaries = self.prepare_for_summarization(old_messages)
        prepared_tokens = self.count_all_tokens(prepared_messages)
        self._debug(
            f"Prepared {len(prepared_messages)} messages for summarization ({prepared_tokens:,} tokens)"
        )
        self._debug(f"Extracted {len(tool_summaries)} tool summaries to append")
        self._debug(self._format_messages_summary(prepared_messages, "PREPARED"))

        # Step 4: Get model for summarization
        model = self.valves.summarizer_model or body.get("model", "")
        self._debug(f"Using model for summarization: {model}")

        # Step 5: Generate summary of conversation (with graceful degradation)
        summary = None
        summarization_failed = False
        try:
            self._debug("Calling Ollama for summarization...")
            prompt = self._build_summarization_prompt(prepared_messages)
            # Show the prompt being sent to the summarization model
            self._debug(f"Summarization prompt ({len(prompt)} chars):\n{prompt}")
            if not prompt:
                self._debug("WARNING: Summarization prompt is empty!")
            summary = await self.summarize_messages(prepared_messages, model)
            if summary:
                self._debug(
                    f"Summarization complete: {len(summary)} chars, {self.count_tokens(summary):,} tokens"
                )
                if not self.valves.dump_full_messages:
                    self._debug(f"Summary:\n{summary}")
            else:
                self._debug(
                    f"WARNING: Summarization returned empty (prompt was {len(prompt)} chars)"
                )
        except Exception as e:
            summarization_failed = True
            self._debug(f"ERROR: Summarization failed: {type(e).__name__}: {e}")
            self._debug(f"Traceback: {traceback.format_exc()}")
            # Graceful degradation: fall back to truncation without summary
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": f"Summarization failed, truncating older messages",
                            "done": False,
                        },
                    }
                )

        # Step 6: Reconstruct messages
        if summarization_failed or not summary:
            # Fallback: just use system + recent messages (truncation without summary)
            self._debug("FALLBACK: Using truncation without summary")
            fallback_content = "[Note: Earlier conversation was truncated due to context limits. Some context may be missing.]"
            # Still include tool summaries if we have them
            if tool_summaries:
                fallback_content += "\n\n[Tool calls from earlier in conversation]\n"
                fallback_content += "\n".join(f"- {ts}" for ts in tool_summaries)
            fallback_note = {"role": "system", "content": fallback_content}
            new_messages = system_messages + [fallback_note] + recent_messages
        else:
            # Normal case: include summary + tool summaries
            summary_content = f"[Previous conversation summary]\n{summary}"
            if tool_summaries:
                summary_content += "\n\n[Tool calls from earlier in conversation]\n"
                summary_content += "\n".join(f"- {ts}" for ts in tool_summaries)
            summary_content += "\n[End of summary - recent messages follow]"
            summary_message = {"role": "system", "content": summary_content}
            new_messages = system_messages + [summary_message] + recent_messages

        body["messages"] = new_messages

        # Log final state
        new_token_count = self.count_all_tokens(new_messages)
        self._debug(f"=== INLET COMPLETE ===")
        self._debug(
            f"Token reduction: {token_count:,} → {new_token_count:,} ({token_count - new_token_count:,} tokens saved)"
        )
        self._debug(f"Message reduction: {len(messages)} → {len(new_messages)}")
        self._debug(self._format_messages_summary(new_messages, "OUTPUT"))

        # Update status
        if __event_emitter__:
            status_msg = f"Summarized: {token_count:,} → {new_token_count:,} tokens"
            if summarization_failed:
                status_msg = f"Truncated: {token_count:,} → {new_token_count:,} tokens (summarization failed)"
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": status_msg,
                        "done": True,
                    },
                }
            )

        return body

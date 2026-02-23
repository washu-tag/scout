# ADR 0013: Temporary Open WebUI Context Summarization Filter

**Date**: 2026-02-23
**Status**: Accepted
**Decision Owner**: Scout Team
**Related**: [ADR 0010: Link Exfiltration Filter](0010-open-webui-link-exfiltration-filter.md)

## Context

Scout Chat uses CPT-OSS in Ollama with a 128K token context window (`num_ctx: 131072`) and preserves the first 32K tokens (`num_keep: 32768`). When conversations exceed this limit, Ollama **silently truncates** older messages from the beginning of the conversation.

### Current Behavior

1. User has long conversation (e.g., multiple Trino queries with large result sets)
2. Context window fills up
3. Ollama discards older messages without warning
4. User experiences conversation "falling apart" - model loses context of earlier discussion
5. No indication to user that truncation occurred

### Industry Approaches

| Platform | Approach |
|----------|----------|
| **ChatGPT** | Auto-injects pre-computed summaries into every prompt |
| **Claude** | On-demand tool-based retrieval (`conversation_search`) |
| **Claude Code** | Auto-compact at 95% context, summarizes trajectory |
| **Goose Desktop** | Configurable: summarize, truncate, clear, or prompt |
| **Open WebUI** | No built-in solution; relies on community functions |

### Tool Call Considerations

Scout Chat uses the Trino MCP tool for SQL queries. Tool results can be very large (hundreds of rows of data). Open WebUI's filter `inlet()` receives messages in a simplified format — there are no `role: "tool"` messages. Instead, tool results are embedded directly in assistant message content as HTML-escaped JSON (e.g., `&quot;results&quot;`). These embedded results:
- Can dominate the context window (a single query result may be tens of thousands of tokens)
- Are difficult to summarize without losing precision
- Contain HTML entities and escaped JSON that require careful parsing

### Knowledge/RAG Considerations

Scout extensively uses Open WebUI's Knowledge feature. RAG content is:
- Retrieved per-query based on relevance
- Injected into system messages
- Can accumulate and duplicate over long conversations

## Decision

**Implement a custom Open WebUI filter function that summarizes conversation history when approaching context limits, with special handling for embedded tool results and RAG content.** Note: this is a temporary solution that isn't particularly efficient (re-summarizes with each subsequent request). It is meant as a starting point for learning. We will ultimately need to move to an approach that persists the changes.

### Key Design Decisions

1. **Base system prompt preserved**: Keep only the FIRST system message (Scout query instructions)
2. **RAG content refreshed**: Subsequent system messages (RAG) are discarded; RAG re-retrieves per-query
3. **Trigger threshold**: 100K tokens (~77% of 128K context), estimated via tiktoken `cl100k_base` encoding (an approximation — the actual Ollama model tokenizer differs, but the margin is sufficient)
4. **Recent messages preserved**: Last 10 messages kept verbatim (dynamically reduced if needed)
5. **User/assistant text**: Summarized via LLM call to Ollama
6. **Embedded tool results in old messages**: Compacted to metadata descriptions including row count, sample data, and error counts — not summarized by the LLM
   - e.g., `[Tool: 247 rows | {"diagnosis": "Malignant neoplasm...", "count": 5}]`
   - Assistant commentary after tool results is preserved when detected
7. **Two-phase output**: The LLM-generated conversation summary and the verbatim tool result descriptions are combined into a single summary system message
8. **Graceful degradation**: If the summarization API call fails (timeout, error, empty response), falls back to truncation with a note rather than failing the request
9. **UI feedback**: Status messages shown to user during summarization
10. **No persistence**: Re-summarize when reopening old chats (simpler implementation)

### Message Structure After Summarization

```
[Base System Prompt]            <- Preserved (first system message only)
[Summary System Message]        <- NEW: Contains both LLM summary and tool descriptions
  ├─ [Previous conversation summary]
  │    <LLM-generated narrative summary>
  ├─ [Tool calls from earlier in conversation]
  │    - [Tool: 50 rows | {"sample": "data"}]
  │    - [Tool: 3 failed queries]
  └─ [End of summary - recent messages follow]
[Recent Messages]               <- Preserved (last N messages, may include fresh RAG)
```

Note: Subsequent system messages (often RAG/knowledge content) are NOT preserved.
RAG will re-retrieve relevant knowledge for the current query automatically.

### Dynamic Keep Count

If the initial split yields nothing to summarize (e.g., only 10 messages but they're huge),
the filter dynamically reduces `messages_to_keep` until it finds something to summarize:

1. Try with configured keep count (e.g., 10)
2. If no old messages, halve the keep count (5)
3. Continue until we find messages to summarize
4. Minimum: keep at least 2 messages

This handles edge cases where tool results are very large.

### Tool Result Handling

Summarizing structured data (SQL query results) via LLM risks:
- Hallucination of data values
- Loss of precision
- Incorrect aggregations

Instead, old assistant messages containing embedded tool results are **compacted** to metadata descriptions. The compaction process:

1. **Detects** embedded tool results via pattern matching on HTML-escaped JSON (`&quot;results&quot;`, `\\"results\\"`, raw JSON arrays, etc.)
2. **Parses** the embedded JSON to extract row counts and a sample first row (with long string values truncated)
3. **Detects** error patterns (e.g., "query execution failed") and counts them
4. **Preserves** any assistant commentary that follows the tool result data
5. **Falls back** to character count if JSON parsing fails

These compacted descriptions serve two purposes:
- They replace large tool results in the messages sent to the LLM for narrative summarization (reducing the summarization input)
- The `[Tool: ...]` lines are also extracted and appended verbatim to the final summary message, so the model retains awareness of what queries were run earlier

Recent tool results (in the preserved recent messages) remain intact so the model can reference actual data.

## Alternatives Considered

### Alternative 1: Install Community Checkpoint Summarization Filter

Use the existing [Checkpoint Summarization Filter](https://openwebui.com/f/projectmoon/checkpoint_summarization_filter) from the community.

**Pros:**
- Immediate availability
- Community-maintained
- Includes ChromaDB persistence

**Cons:**
- External dependency
- Does not handle embedded tool results (assumes `role: "tool"` messages which Open WebUI's inlet doesn't provide)
- May not handle RAG content properly
- Less control over summarization behavior

**Verdict:** Rejected in favor of custom solution for better control over tool and RAG handling.

### Alternative 2: RAG-Based Conversation Memory

Store all conversation history in a vector database, retrieve relevant context on demand.

**Pros:**
- Scales indefinitely
- Semantic retrieval of relevant past context
- No information loss

**Cons:**
- Significant infrastructure complexity
- Requires additional vector database
- Higher latency for retrieval

**Verdict:** Considered for future enhancement, too complex for initial implementation.

### Alternative 3: Increase Context Window / Accept Truncation

Simply accept current behavior with larger `num_keep` value.

**Pros:**
- Zero implementation effort
- No additional latency

**Cons:**
- Users still experience context loss
- No user feedback when truncation occurs
- Poor UX for extended conversations

**Verdict:** Rejected - does not address core user experience problem.

### Alternative 4: Preserve All System Messages

Keep all system messages (including RAG content) intact.

**Pros:**
- Preserves all injected knowledge
- Simple logic

**Cons:**
- RAG content duplicates across queries
- Wastes context window on redundant content
- RAG re-retrieves relevant content anyway

**Verdict:** Rejected - RAG is designed to retrieve fresh relevant content per-query.

## Implementation

### Filter Structure

The filter implements:
- `inlet()`: Intercepts messages before sending to LLM
- Token counting via tiktoken (`cl100k_base` encoding — an approximation for the actual model's tokenizer, with sufficient margin from the 77% threshold)
- Detection and compaction of embedded tool results in assistant messages
- Separate handling for text messages vs embedded tool results
- Status messages via `__event_emitter__`
- Async Ollama API call for summarization
- Graceful fallback to truncation on API failure

### Message Processing Flow

```
1. Count tokens in all messages
2. If under threshold → pass through unchanged
3. If over threshold:
   a. Show status: "Summarizing conversation (X tokens)..."
   b. Extract first system message only (base prompt)
   c. Split remaining into old vs recent (keep last N, dynamically reduce if needed)
   d. Prepare old messages for summarization:
      - Skip system messages (RAG will re-retrieve)
      - For assistant messages with embedded tool results exceeding threshold:
        → Compact to metadata description (row count, sample row, error count)
        → Extract [Tool: ...] line for verbatim inclusion in output
        → Preserve any commentary text after the tool result data
      - Pass through user messages and small assistant messages unchanged
   e. Send prepared messages to Ollama for narrative summary
   f. Combine LLM summary + verbatim tool descriptions into one system message
   g. Reconstruct: [base system] + [summary message] + [recent messages]
   h. Show status: "Summarized: X → Y tokens"
   i. On summarization failure: fall back to truncation with note
```

### Deployment

Installed via Open WebUI Admin UI (same pattern as Link Sanitizer Filter):
1. Admin Panel → Functions → New Function
2. Paste filter code
3. Configure valves (token threshold, messages to keep, Ollama URL, etc.)
4. Enable globally

## Consequences

### Positive

- Users get feedback when context is being managed
- Conversation coherence improves for long sessions
- Tool results handled appropriately (compacted with metadata, not hallucinated summaries)
- RAG content handled efficiently (no duplicates, fresh retrieval)
- Base system prompt preserved
- Graceful degradation on failure (truncation fallback)
- Follows existing Scout filter deployment pattern

### Negative

- Summarization adds latency (~5-10 seconds)
- Summary may lose some conversation nuance
- Re-summarization required when reopening old chats
- Depends on LLM quality for summarization
- Token counting is approximate (tiktoken vs actual model tokenizer)

### Risks

- Summarization model may hallucinate (mitigated by keeping recent context intact)
- Very long conversations may require multiple summarization rounds
- httpx timeout may fail for very large summarization requests (mitigated by graceful fallback)
- Tool result detection relies on pattern matching against Open WebUI's HTML-escaped format, which could change across versions

## References

- [Open WebUI Filter Functions Documentation](https://docs.openwebui.com/features/plugin/functions/filter/)
- [Open WebUI RAG Documentation](https://docs.openwebui.com/features/rag/)
- [Checkpoint Summarization Filter](https://openwebui.com/f/projectmoon/checkpoint_summarization_filter)
- [Context Engineering for Agents (LangChain)](https://rlancemartin.github.io/2025/06/23/context_engineering/)
- [Manus Agent Context Engineering](https://rlancemartin.github.io/2025/10/15/manus/)
- [Ollama Context Length Documentation](https://docs.ollama.com/context-length)
- [Open WebUI Event Emitter Docs](https://docs.openwebui.com/features/plugin/tools/development/)

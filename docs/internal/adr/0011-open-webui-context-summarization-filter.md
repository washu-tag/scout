# ADR 0011: Open WebUI Context Summarization Filter

**Date**: 2025-12-18  
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

Scout Chat uses the Trino MCP tool for SQL queries. Tool results can be very large (hundreds of rows of data). These results:
- Are stored as `role: "tool"` messages
- Count against context window
- Are difficult to summarize without losing precision

### Knowledge/RAG Considerations

Scout extensively uses Open WebUI's Knowledge feature. RAG content is:
- Retrieved per-query based on relevance
- Injected into system messages
- Can accumulate and duplicate over long conversations

## Decision

**Implement a custom Open WebUI filter function that summarizes conversation history when approaching context limits, with special handling for tool call results and RAG content.**

### Key Design Decisions

1. **Base system prompt preserved**: Keep only the FIRST system message (Scout query instructions)
2. **RAG content refreshed**: Subsequent system messages (RAG) are discarded; RAG re-retrieves per-query
3. **Trigger threshold**: 100K tokens (~77% of 128K context)
4. **Recent messages preserved**: Last 10 messages kept verbatim
5. **User/assistant text**: Summarized via LLM call
6. **Tool call results**: Compacted to brief descriptions (not summarized)
   - e.g., `[Tool result: returned 247 rows]`
7. **UI feedback**: Status messages shown to user during summarization
8. **No persistence**: Re-summarize when reopening old chats (simpler implementation)

### Message Structure After Summarization

```
[Base System Prompt]            <- Preserved (first system message only)
[Conversation Summary]          <- NEW: Generated summary of old conversation
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

### Tool Result Handling Rationale

Summarizing structured data (SQL query results) risks:
- Hallucination of data values
- Loss of precision
- Incorrect aggregations

Instead, old tool results are replaced with **metadata descriptions** that preserve:
- What tool was called
- Approximate result size
- Error states if applicable

Recent tool results remain intact so the model can reference actual data.

## Alternatives Considered

### Alternative 1: Install Community Checkpoint Summarization Filter

Use the existing [Checkpoint Summarization Filter](https://openwebui.com/f/projectmoon/checkpoint_summarization_filter) from the community.

**Pros:**
- Immediate availability
- Community-maintained
- Includes ChromaDB persistence

**Cons:**
- External dependency
- May not handle tool results appropriately for Scout's use case
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
- Token counting via tiktoken (cl100k_base encoding)
- Separate handling for text messages vs tool results
- Status messages via `__event_emitter__`
- Async Ollama API call for summarization

### Message Processing Flow

```
1. Count tokens in all messages
2. If under threshold → pass through unchanged
3. If over threshold:
   a. Show status: "Summarizing conversation..."
   b. Extract first system message only (base prompt)
   c. Split remaining messages: old vs recent (keep last N)
   d. For old messages:
      - User/assistant text → send to LLM for summarization
      - Tool results → replace with brief description
      - System messages (RAG) → discard (will be re-retrieved)
   e. Create summary message
   f. Reconstruct: [base system] + [summary] + [recent]
   g. Show status: "Summarized: X → Y tokens"
```

### Deployment

Installed via Open WebUI Admin UI (same pattern as Link Sanitizer Filter):
1. Admin Panel → Functions → New Function
2. Paste filter code
3. Configure valves
4. Enable globally

## Consequences

### Positive

- Users get feedback when context is being managed
- Conversation coherence improves for long sessions
- Tool results handled appropriately (no hallucinated summaries)
- RAG content handled efficiently (no duplicates, fresh retrieval)
- Base system prompt preserved
- Follows existing Scout filter deployment pattern

### Negative

- Summarization adds latency (~5-10 seconds)
- Summary may lose some conversation nuance
- Re-summarization required when reopening old chats
- Depends on LLM quality for summarization

### Risks

- Summarization model may hallucinate (mitigated by keeping recent context intact)
- Very long conversations may require multiple summarization rounds
- httpx timeout may fail for very large summarization requests

## References

- [Open WebUI Filter Functions Documentation](https://docs.openwebui.com/features/plugin/functions/filter/)
- [Open WebUI RAG Documentation](https://docs.openwebui.com/features/rag/)
- [Checkpoint Summarization Filter](https://openwebui.com/f/projectmoon/checkpoint_summarization_filter)
- [Context Engineering for Agents (LangChain)](https://rlancemartin.github.io/2025/06/23/context_engineering/)
- [Manus Agent Context Engineering](https://rlancemartin.github.io/2025/10/15/manus/)
- [Ollama Context Length Documentation](https://docs.ollama.com/context-length)
- [Open WebUI Event Emitter Docs](https://docs.openwebui.com/features/plugin/tools/development/)

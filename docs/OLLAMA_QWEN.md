# Ollama + Qwen

## 1. Install Ollama

Install Ollama normally and verify:

```bash
ollama list
```

## 2. Pull Qwen

Default SCAG model:

```bash
ollama pull qwen3:8b
```

You can use another installed Qwen model by setting:

```text
SCAG_QWEN_MODEL=qwen3:14b
```

## 3. Start Ollama

Ollama normally exposes:

```text
http://localhost:11434
```

SCAG calls:

```text
POST /api/chat
```

with:

- `stream: false`
- `format: json`
- low temperature
- complete timestamped transcript
- candidate list

## 4. Why the complete transcript is sent

The candidate detector is intentionally high-recall. It is not supposed to decide what is viral.

Qwen gets the entire transcript so it can understand:
- what led to the moment;
- what happened afterward;
- callbacks;
- setup/punchline;
- reactions;
- whether the candidate is actually interesting;
- whether a moment needs additional context.

## 5. Context padding

Qwen chooses the core moment.

SCAG then expands it:

```text
core_start - 10 seconds
core_end   + 10 seconds
```

The final render boundaries are clipped to the available transcript duration.

This keeps the selected moment from feeling abruptly cut while keeping Qwen's editorial boundary separate from the rendering boundary.

## 6. Failure behavior

If Ollama is unreachable, SCAG raises a clear error instead of silently pretending that Qwen selected clips.

If Qwen returns invalid JSON, SCAG rejects the response instead of silently generating fake selections.

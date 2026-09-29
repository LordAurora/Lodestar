"""Prompts sent to the local model.

Keeping prompts in one module makes them easy to read, tweak and compare in
the tutorial. Small local models follow short, explicit rules best.
"""

from __future__ import annotations

from app.retrieval import RetrievedChunk

SYSTEM_PROMPT = """You are Lodestar, an assistant that answers questions about a code base.

Rules:
- Answer ONLY from the numbered code snippets provided by the user. Do not use outside knowledge about this code base.
- Cite the snippets you use inline with their number in square brackets, like [1] or [2][3].
- If the snippets do not contain enough information, say clearly what is missing instead of guessing.
- Be concise. Use Markdown, and put code in fenced code blocks with a language tag."""

ANSWER_TEMPLATE = """Code snippets:

{context}

Question: {question}

Answer from the snippets only. Put the snippet number in square brackets after each fact, like [1]."""

# A worked example, sent as a fake earlier turn. Small models copy the
# citation style they are shown far more reliably than one they are told about.
EXAMPLE_QUESTION = """Code snippets:

[1] app/cache.py:10-11 (get_cached)
```python
def get_cached(key):
    return _store.get(key)
```

[2] app/config.py:1-1
```python
CACHE_TTL = 300
```

Question: How long are cache entries kept?"""

EXAMPLE_ANSWER = "Cache entries are kept for 300 seconds, set by `CACHE_TTL` [2]. They are read back with `get_cached` [1]."

REWRITE_PROMPT = """Rewrite the user's latest question as a single standalone question that can be understood without the conversation. Keep code identifiers exactly as written. Reply with the rewritten question only.

Conversation:
{history}

Latest question: {question}

Standalone question:"""

NO_ANSWER_MESSAGE = "I couldn't find anything relevant to this question in the indexed code."


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Label each chunk ``[n] path:start-end`` so the model can cite it."""
    blocks = []
    for n, chunk in enumerate(chunks, start=1):
        label = f"[{n}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
        if chunk.symbol_name:
            label += f" ({chunk.symbol_name})"
        blocks.append(f"{label}\n```{chunk.language}\n{chunk.content}\n```")
    return "\n\n".join(blocks)


def build_answer_messages(question: str, chunks: list[RetrievedChunk]) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": EXAMPLE_QUESTION},
        {"role": "assistant", "content": EXAMPLE_ANSWER},
        {
            "role": "user",
            "content": ANSWER_TEMPLATE.format(context=format_context(chunks), question=question),
        },
    ]


def build_rewrite_messages(question: str, history: list[dict]) -> list[dict]:
    """``history`` is a list of {"role", "content"} messages (the last two turns)."""
    lines = []
    for message in history:
        content = message["content"]
        if len(content) > 600:  # long answers only need their gist
            content = content[:600] + " ..."
        lines.append(f"{message['role'].capitalize()}: {content}")
    prompt = REWRITE_PROMPT.format(history="\n".join(lines), question=question)
    return [{"role": "user", "content": prompt}]

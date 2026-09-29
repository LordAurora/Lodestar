# Lodestar: Build Prompt

> Paste this whole file into your coding agent (Cursor, Claude Code, Copilot Agent, etc.) as the task prompt.

---

## 0. Role and goal

You are a senior full-stack engineer. Build **Lodestar**, a **100% local** code-base assistant. The user points it at a local Git repository, it indexes the code, and the user can ask questions such as *"How does authentication work in this project?"*. Answers must be grounded in the code and cite `file:line` references.

The LLM runs on-device through **Microsoft Foundry Local**. No code, prompt, or embedding may ever leave the machine. There must be no calls to cloud APIs, no telemetry, and no CDN-loaded assets at runtime.

This project is also the subject of a tutorial called *"Building Your First Local RAG Application with Foundry Local"*, so the code must be **readable, well-commented, and easy to follow**. Prefer clarity over cleverness. Implement the RAG pipeline yourself (chunking, embedding, retrieval, prompting). **Do not use LangChain or LlamaIndex.**

---

## 1. Tech stack

**Backend**
- Python 3.11+, **FastAPI**, Uvicorn, Pydantic v2
- **Foundry Local** for chat completions. Use the official `foundry-local-sdk` to start the service and resolve the endpoint, then talk to it through the OpenAI-compatible API with the `openai` client (`base_url` from the SDK, dummy API key).
- **Embeddings:** first check the Foundry Local docs and SDK to see whether an embedding model is available. If not, fall back to a local ONNX or `fastembed` embedding model. Prefer a code-aware model (for example `jinaai/jina-embeddings-v2-base-code`) and fall back to `BAAI/bge-small-en-v1.5`. Hide this behind an `Embedder` interface so it can be swapped by config.
- **Storage:** a single **SQLite** file per indexed repo.
  - Vectors: store as BLOB (float32) and compute cosine similarity with NumPy, or use `sqlite-vec` if it installs cleanly.
  - Keyword search: **SQLite FTS5** (BM25).
- **Code parsing:** `tree-sitter` (via `tree-sitter-language-pack`) for Python, JavaScript, TypeScript, Go, Java, and C#. Unsupported languages use a sliding-window line chunker.
- Tooling: `uv` or `pip` with `pyproject.toml`, `ruff`, `pytest`.

**Frontend**
- **React + TypeScript + Vite**
- **Tailwind CSS** and **shadcn/ui** (Radix primitives), `lucide-react` icons
- **TanStack Query** for server state
- `react-markdown` + `shiki` for rendering answers and code blocks (bundle locally)
- Font: **Inter**, self-hosted (no Google Fonts)

---

## 2. Core features

### 2.1 Repository indexing
- The user enters a local folder path (or picks a previously indexed repo).
- Walk the tree, respecting `.gitignore` and skipping `node_modules`, `.git`, `dist`, `build`, `venv`, binaries, lockfiles, and files over 1 MB.
- **Incremental indexing:** hash each file (SHA-256). On re-index, only process new or changed files and remove chunks of deleted files.
- Show live progress in the UI through Server-Sent Events: files scanned, chunks created, chunks embedded, elapsed time.

### 2.2 Code-aware chunking
- Chunk at **function / method / class** boundaries with tree-sitter.
- Large classes split into method-level chunks. Keep the class signature as context.
- Each chunk stores: `repo_id`, `file_path`, `language`, `symbol_name`, `symbol_kind`, `start_line`, `end_line`, `content`, `file_hash`.
- Prepend a small header to the text that gets embedded, for example `# path/to/file.py :: ClassName.method_name`, so the file path and symbol contribute to the embedding.
- Target 100–400 tokens per chunk. Very small adjacent chunks (imports, constants) are merged into one "file header" chunk.
- Fallback for unsupported files: sliding window of ~60 lines with 10 lines of overlap.

### 2.3 Hybrid retrieval
1. Embed the question and take the top **N=20** by cosine similarity.
2. Take the top **N=20** by BM25 (FTS5), using identifier-friendly tokenization (split `camelCase` and `snake_case`).
3. Merge with **Reciprocal Rank Fusion (RRF)**.
4. Keep the top **K** (default 6) for the prompt.
5. Return both the fused score and the raw cosine score for each result.

### 2.4 Grounded answers with "I don't know"
- If the best cosine score is below a configurable **relevance threshold** (default `0.35`, tunable in Settings), **do not call the LLM**. Return: *"I couldn't find anything relevant to this question in the indexed code."* and show the closest matches as suggestions.
- Otherwise build the prompt with the retrieved chunks, each labelled `[n] path:start-end`.
- The system prompt must instruct the model to:
  - answer **only** from the provided snippets,
  - cite sources inline as `[1]`, `[2]`,
  - say what is missing if the snippets are insufficient instead of guessing,
  - keep answers concise and use code blocks for code.
- Stream tokens to the UI (SSE). After the answer completes, parse citations and link each `[n]` to its source chunk.

### 2.5 Sources and code preview
- Every answer shows a **Sources** panel listing the cited chunks (file path, lines, symbol, score).
- Clicking a source opens a side drawer with syntax-highlighted code and highlighted line range.

### 2.6 Settings
- Chat model: list the models available in Foundry Local, let the user pick one, and show download or load status.
- Top-K, relevance threshold, and hybrid search on/off.
- Embedding model shown (read-only).
- A **"Local only"** status indicator that confirms no network calls are being made.

### 2.7 Conversations
- Multi-turn chat per repository, persisted in SQLite.
- For follow-up questions, rewrite the question into a standalone query using the last 2 turns before retrieval.
- Conversation list in the sidebar, with rename and delete.

---

## 3. API design (FastAPI)

```
GET    /api/health                        -> service + Foundry Local status
GET    /api/models                        -> available chat models
POST   /api/repos                         -> { path } register a repo
GET    /api/repos                         -> list repos with stats
DELETE /api/repos/{id}                    -> remove repo + index
POST   /api/repos/{id}/index              -> start indexing (returns job id)
GET    /api/repos/{id}/index/stream       -> SSE progress events
GET    /api/repos/{id}/conversations      -> list conversations
POST   /api/repos/{id}/chat               -> SSE: retrieval event, token events, done event
GET    /api/chunks/{chunk_id}             -> chunk content + surrounding lines
GET    /api/settings / PUT /api/settings
```

The `/chat` SSE stream emits, in order: `retrieval` (chunks and scores), `token` (repeated), `done` (final answer + citations), or `no_answer` (below threshold).

---

## 4. Project structure

```
lodestar/
├── README.md
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py                # SQLite schema + helpers
│   │   ├── foundry.py           # Foundry Local start/connect, model listing
│   │   ├── embeddings.py        # Embedder interface + implementations
│   │   ├── chunking/
│   │   │   ├── treesitter.py
│   │   │   └── fallback.py
│   │   ├── indexer.py           # walk, hash, chunk, embed, store
│   │   ├── retrieval.py         # vector + BM25 + RRF
│   │   ├── prompts.py           # system prompt, query rewrite prompt
│   │   ├── rag.py               # orchestrates retrieve -> threshold -> generate
│   │   └── routes/
│   └── tests/
└── frontend/
    ├── package.json
    └── src/
        ├── components/
        ├── pages/
        ├── lib/
        └── styles/
```

Keep each file focused. Add short module docstrings explaining **why** the module exists, since the code will be shown in a tutorial.

---

## 5. UI / UX design: SaaS style

**Overall feel:** clean, modern B2B SaaS dashboard in the spirit of Linear, Vercel, or Stripe. Calm, spacious, and professional.

**Hard rules**
- **NO gradients anywhere.** No gradient backgrounds, buttons, text, borders, or glows. Use flat, solid colors only.
- No glassmorphism, no heavy shadows, no neon effects, and no decorative illustrations.
- One accent color only (indigo `#4F46E5` or similar), used for primary buttons, links, focus rings, and active states.

**Design tokens**
- Light theme: background `#FFFFFF`, app surface `#F9FAFB`, borders `#E5E7EB`, text `#111827`, muted text `#6B7280`.
- Dark theme: background `#0B0D10`, surface `#12151A`, borders `#232830`, text `#F3F4F6`, muted `#9CA3AF`. Provide a light/dark toggle that respects `prefers-color-scheme`.
- Semantic colors (flat): success green, warning amber, error red.
- Radius: 8px for inputs and buttons, 12px for cards. Shadows: at most a single very subtle `shadow-sm`. Prefer 1px borders.
- Spacing on an 4/8px scale. Generous whitespace.
- Type: Inter, 14px base, 12px for meta, 20–24px for page titles, weights 400/500/600. Use a monospace font (JetBrains Mono, self-hosted) for code.

**Layout**
- **Left sidebar (240px, collapsible):** logo and product name, repository switcher, conversation list, and at the bottom Settings and the theme toggle.
- **Top bar:** repository name, index status badge ("Indexed · 1,284 chunks · 2 min ago"), "Re-index" button.
- **Main area:** chat thread centered at a max width of ~760px. The composer is docked at the bottom with a rounded input, a send button, and a hint line ("Answers are generated locally").
- **Right drawer (420px):** source code preview, opens on citation click.

**Screens**
1. **Onboarding / empty state:** a card with a folder path input, an "Add repository" button, and three short steps (Add repo → Index → Ask). Also shows Foundry Local status and model readiness.
2. **Indexing:** progress card with a flat progress bar, live counters, and a scrolling log of recent files.
3. **Chat:** user messages in a subtle surface-colored bubble, assistant messages as plain text with markdown. Citation chips like `[1]` are small flat pills. Below each answer show a collapsible "Sources (4)" list. Include copy and regenerate actions. Show 3 suggested-question chips in the empty state.
4. **No-answer state:** a neutral info card (not an error) explaining nothing relevant was found, with closest matches listed.
5. **Settings:** a page with grouped cards (Model, Retrieval, About/Privacy), using standard form controls.

**Components and behavior**
- Use shadcn/ui components (Button, Input, Card, Badge, Tabs, Dialog, Sheet, Tooltip, Select, Slider, Switch, Skeleton, Toast).
- Skeleton loaders while loading, and a subtle blinking caret while streaming.
- Keyboard: `Enter` to send, `Shift+Enter` for a new line, `Cmd/Ctrl+K` to focus the composer.
- Fully responsive down to tablet width, with the sidebar collapsing to an icon rail. Accessible: visible focus rings, ARIA labels, and WCAG AA contrast.
- All UI strings live in one `strings.ts` file so the UI can be translated later (default: English).

---

## 6. Quality requirements

- **Privacy:** add a test that asserts no outbound network requests are made other than to `localhost`. Do not load fonts, icons, or scripts from external URLs.
- **Errors:** friendly messages when Foundry Local is not installed or running, when a model is not downloaded, or when the repo path is invalid. Provide actionable next steps.
- **Performance:** batch embedding calls, run indexing in a background task, and never block the event loop with CPU-heavy work (use a thread pool).
- **Tests (pytest):** chunker (per language), incremental indexing, RRF fusion, threshold behavior (LLM must not be called below the threshold), and citation parsing. Use a fake Embedder and a fake LLM client in tests.
- **Evaluation script:** `scripts/eval.py` reads a small YAML file of `question → expected file(s)` for a sample repo and reports **hit@K** and **MRR** for vector-only, BM25-only, and hybrid retrieval. Print a results table. This will be used in the tutorial.
- **Lint/format:** `ruff` for Python, ESLint + Prettier for the frontend.

---

## 7. Deliverables

1. Full working source code following the structure above.
2. `README.md` with: what it is, architecture diagram (ASCII or Mermaid), prerequisites (Foundry Local install), setup for backend and frontend, how to run, configuration options, and troubleshooting.
3. A `Makefile` (or scripts) with `make setup`, `make dev`, `make test`, `make eval`.
4. A sample small repo under `examples/` for a quick demo.
5. `.env.example` with all configurable values.

---

## 8. Working method

1. First, read the Foundry Local documentation and confirm the exact SDK calls for starting the service, listing and loading models, and whether embeddings are supported. State your findings and any assumptions before writing code.
2. Then propose a short plan and build in this order: backend skeleton → DB schema → chunking → embeddings → indexer → retrieval → RAG + threshold → API → frontend shell → chat → sources drawer → settings → tests → eval → README.
3. After each milestone, run the tests and fix failures before moving on.
4. Do not invent APIs. If something is uncertain, check the docs or the installed package and say so.
5. Keep commits small and messages clear.

Start now with step 1.

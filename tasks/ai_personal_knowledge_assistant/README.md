# AI Personal Knowledge Assistant

A document chatbot. You upload PDF, DOCX or TXT files, then ask questions about them in plain
language. Answers come only from passages retrieved from your documents (RAG over a persistent
ChromaDB collection) and list their sources by filename and page. When the documents don't
contain the answer, the assistant says so instead of guessing. A single AI agent, written as a
manual OpenAI tool-calling loop (no LangChain), can also list, search and answer from the
documents, and the UI shows which tools it used. A FastAPI backend serves the HTTP API and a
Streamlit app provides the UI.

## Features

| Brief requirement | How it is met |
|---|---|
| Upload PDF / DOCX / TXT | `POST /documents/upload`. Checks the extension and the file's magic bytes, the size limit (`MAX_UPLOAD_MB`), empty or textless files, damaged files and duplicates (SHA-256) |
| RAG pipeline | Text extracted with pypdf / python-docx → overlapping, boundary-aware chunks (800 chars, 150 overlap) → `all-MiniLM-L6-v2` embeddings → **persistent ChromaDB** (cosine) |
| Answers with sources | `POST /chat/ask` returns the answer with `[n]` citations and structured sources: filename, page, chunk, relevance score, snippet |
| No invented answers | Chunks below `MIN_RELEVANCE` are dropped. If none are left, a fixed "couldn't find this" reply is returned **without calling the LLM**. The prompt allows answers from the numbered excerpts only |
| Single AI agent | `POST /agent/chat`. Manual tool-calling loop on the OpenAI Responses API over three tools: `list_documents`, `search_documents`, `answer_from_documents`. Every call is returned in `tool_calls` |
| Metadata + chat history | SQLAlchemy: SQLite by default, MySQL by changing `DATABASE_URL`. Stores documents, chat sessions, and messages with their sources and tool calls |
| Scanned PDFs (optional) | With `OCR_ENABLED=true` and the `ocr` extra, PDF pages without a text layer are read with Tesseract OCR and keep their page numbers. See [OCR for scanned PDFs](#optional-ocr-for-scanned-pdfs) |
| UI | Streamlit: upload and document list in the sidebar, an "Ask documents" tab and an "Agent chat" tab, sources in an expander, "Tools used" under each agent reply |

## Tech stack

| Concern | Choice |
|---|---|
| Language / packaging | Python 3.12, [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`) |
| API | FastAPI + Pydantic v2, pydantic-settings for configuration |
| UI | Streamlit (talks to the API over HTTP with httpx) |
| LLM | OpenAI Responses API, default model `gpt-5.4-mini` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local, CPU, 384 dimensions, normalized) |
| Vector store | ChromaDB `PersistentClient`, cosine distance |
| Relational DB | SQLAlchemy 2: SQLite (default) or MySQL (`pymysql`) |
| Text extraction | pypdf, python-docx, TXT with encoding detection; optional OCR with pdf2image (poppler) + pytesseract (Tesseract) |
| Tests / lint | pytest, ruff |

## Architecture

One folder per responsibility, under `src/ai_personal_knowledge_assistant/`:

```
ai_personal_knowledge_assistant/
├── core/         settings (.env), domain exceptions, logging
├── storage/      SQLAlchemy models + repositories: documents, chat sessions/messages
├── vectorstore/  MiniLM embedder and the persistent ChromaDB store
├── ingestion/    validation, text extraction, chunking, ingestion pipeline
├── rag/          retriever (relevance threshold), OpenAI client, grounded answerer
├── agent/        tool schemas + execution, agent prompt, manual tool-calling loop
├── api/          FastAPI app, dependency wiring, schemas, routes (HTTP only)
├── frontend/     Streamlit app + thin HTTP client for the API
└── cli.py        console entry points `pka-api` and `pka-ui`
```

Dependencies point one way only:

```
api ──► agent ──► rag ──► ingestion / storage / vectorstore ──► core
frontend ──HTTP──► api
```

Nothing lower in the chain imports from a layer above it. The frontend never imports the backend
internals; it only calls the HTTP API. The routes contain no business logic: each domain
exception maps to one HTTP status in `api/main.py`.

### Upload flow

```mermaid
flowchart LR
    U[Client / Streamlit] -->|POST /documents/upload| V[Validate: extension, magic bytes,<br/>size, empty, duplicate SHA-256]
    V -->|reject| E[4xx with detail]
    V --> S[Save file to data/uploads<br/>DB row: processing]
    S --> X[Extract pages<br/>pypdf / python-docx / txt]
    X --> C[Chunk: 800 chars,<br/>150 overlap, page kept]
    C --> M[Embed with MiniLM]
    M --> CH[(ChromaDB<br/>data/chroma)]
    CH --> R[DB row: ready,<br/>page + chunk counts]
    R --> O[201 DocumentOut]
    X -. failure .-> RB[Roll back: delete vectors,<br/>row and saved file]
    M -. failure .-> RB
```

### Question flow (`/chat/ask` and `/agent/chat`)

```mermaid
flowchart TD
    A[POST /chat/ask] --> ANS
    G[POST /agent/chat] --> L{Agent loop<br/>OpenAI Responses API,<br/>max AGENT_MAX_ROUNDS}
    L -->|list_documents| DB[(SQLite / MySQL)]
    L -->|search_documents| RET
    L -->|answer_from_documents| ANS
    L -->|final reply| GR[reply + tool_calls + sources]

    ANS[Grounded answerer] --> RET[Retriever: embed question,<br/>query Chroma top_k]
    RET --> CH[(ChromaDB)]
    RET --> F{Any chunk with<br/>score ≥ MIN_RELEVANCE?}
    F -->|no| NF["I couldn't find this in your<br/>uploaded documents." — LLM not called]
    F -->|yes| LLM[OpenAI: answer only from<br/>numbered excerpts, cite n]
    LLM --> AR[answer + sources<br/>filename, page, score]

    AR --> H[Save exchange to chat history]
    NF --> H
    GR --> H
```

Both endpoints load recent history for the `session_id` (up to `CHAT_HISTORY_LIMIT` messages)
and store the new exchange afterwards. If the request fails, nothing is stored.

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (developed with uv 0.12.3)
- Python 3.12 or newer. uv installs it for you if it's missing (`uv python install 3.12`)
- An OpenAI API key, for `/chat/ask` and `/agent/chat` only. Uploading and listing work without one
- About 90 MB of disk for the MiniLM model, which downloads to the Hugging Face cache on first use

### Install

```bash
cd tasks/ai_personal_knowledge_assistant
uv sync
```

> **Workspace caveat.** This project is a member of the uv workspace defined in the repository
> root's `pyproject.toml`, and all members share one `.venv` at the repo root. `uv sync` run
> inside this folder installs only this project's packages and **removes** the packages other
> members need from the shared environment. To keep every member working, sync from the repo
> root instead:
>
> ```bash
> # from the repository root
> uv sync --all-packages
> ```
>
> `uv run ...` works from either folder afterwards.

### Configure

```bash
cp .env.example .env
# then edit .env and set OPENAI_API_KEY=sk-...
```

Every setting is optional and has a default. The values come from environment variables first,
then `.env`, then the defaults. Relative paths are resolved against this project folder, not the
current working directory.

| Setting | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | *(unset)* | Needed for `/chat/ask` and `/agent/chat`. When it is unset, those endpoints return 503 |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model for both the answerer and the agent |
| `DATABASE_URL` | `sqlite:///data/app.db` | SQLAlchemy URL for metadata and chat history |
| `CHROMA_DIR` | `data/chroma` | ChromaDB persistence folder |
| `CHROMA_COLLECTION` | `documents` | Chroma collection name |
| `UPLOAD_DIR` | `data/uploads` | Where uploaded files are saved |
| `MAX_UPLOAD_MB` | `10` | Largest accepted upload |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Sentence Transformers model |
| `CHUNK_SIZE` | `800` | Chunk length in characters. Stays under MiniLM's 256-token input limit |
| `CHUNK_OVERLAP` | `150` | Characters shared by neighbouring chunks. Must be smaller than `CHUNK_SIZE` |
| `TOP_K` | `5` | Chunks retrieved per question |
| `MIN_RELEVANCE` | `0.25` | Minimum cosine similarity (0..1) for a chunk to count as evidence. See below |
| `AGENT_MAX_ROUNDS` | `6` | Tool-calling rounds before the agent gives up (502) |
| `CHAT_HISTORY_LIMIT` | `20` | Past messages of a session sent back to the model |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Bind address of `pka-api` |
| `API_BASE_URL` | `http://127.0.0.1:8000` | Where the Streamlit UI finds the API |
| `OCR_ENABLED` | `false` | OCR for PDF pages without a text layer. Needs the `ocr` extra plus tesseract and poppler (see [OCR for scanned PDFs](#optional-ocr-for-scanned-pdfs)) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |

**Why `MIN_RELEVANCE=0.25`.** The value was calibrated on MiniLM similarity scores for the
sample documents. Questions the documents answer score **0.34–0.59** against their best chunk,
and questions they don't cover score **≤ 0.14**. 0.25 sits in the gap between the two ranges.
Raise it if off-topic answers get through; lower it if real questions come back "not found".

## Run

Start the API and the UI in two terminals:

```bash
uv run pka-api      # FastAPI on http://127.0.0.1:8000, Swagger UI at http://127.0.0.1:8000/docs
uv run pka-ui       # Streamlit on http://localhost:8501
```

`uv run pka-api --help` shows the options: `--host`, `--port`, and `--reload` (restart on code
changes). Extra arguments to `pka-ui` are passed through to `streamlit run`.

To regenerate the sample documents in `sample_docs/` (the output is deterministic):

```bash
uv run python scripts/make_sample_docs.py
```

Runtime state lives in `data/` (git-ignored): `app.db`, `chroma/`, `uploads/`. Delete that
folder to start from an empty knowledge base.

## API reference

Every error body has the shape `{"detail": "..."}`. Interactive docs are at `/docs`.

### `POST /documents/upload`: upload and index a file

```bash
curl -F "file=@sample_docs/HR_Policy.pdf" http://127.0.0.1:8000/documents/upload
```

`201 Created`. The request returns once the document is indexed and ready to search:

```json
{"id":1,"filename":"HR_Policy.pdf","file_type":"pdf","size_bytes":3376,"page_count":3,
 "chunk_count":4,"status":"ready","created_at":"2026-11-25T02:14:16.624863Z"}
```

`page_count` is `null` for DOCX and TXT.

### `GET /documents`: list documents, newest first

```bash
curl http://127.0.0.1:8000/documents                   # ready documents (default)
curl "http://127.0.0.1:8000/documents?status=failed"   # processing | ready | failed
```

Returns a JSON array of the same `DocumentOut` objects as the upload.

### `POST /chat/ask`: grounded answer with sources

```bash
curl -X POST http://127.0.0.1:8000/chat/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How many casual leaves are allowed?"}'
```

```json
{"answer":"Employees are entitled to 12 days of casual leave per calendar year. [1]",
 "is_grounded":true,
 "sources":[{"citation":1,"document_id":1,"filename":"HR_Policy.pdf","page":2,"chunk_index":1,
             "score":0.344,"snippet":"…12 days of casual leave per calendar year…"}],
 "session_id":"e844018f-…"}
```

A question the documents don't cover, such as `"What is the capital of France?"`:

```json
{"answer":"I couldn't find this in your uploaded documents.","is_grounded":false,
 "sources":[],"session_id":"…"}
```

Optional fields: `session_id` continues a conversation, and `document_ids` restricts the search
to those documents.

### `POST /agent/chat`: chat with the tool-calling agent

```bash
curl -X POST http://127.0.0.1:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "List my uploaded documents."}'
```

```json
{"reply":"You have 3 uploaded documents: …",
 "tool_calls":[{"tool":"list_documents","arguments":{},"is_error":false,"summary":"3 documents"}],
 "sources":[],"session_id":"…"}
```

`tool_calls` lists every tool the agent ran, in order. `sources` carries the excerpts behind any
`answer_from_documents` call. Send `session_id` back to continue the conversation.

| Tool | Used for |
|---|---|
| `list_documents` | Which files exist, how many, when they were uploaded, their ids |
| `search_documents` | Ranked passages for a query (exploring, locating, quoting) |
| `answer_from_documents` | Direct factual questions. Calls the same grounded answerer as `/chat/ask` |

### `GET /health`: liveness and content counts

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status":"ok","ready_documents":3,"stored_chunks":7}
```

(Example values. The counts reflect what you have uploaded.)

### Error codes

| Status | Meaning |
|---|---|
| 415 | Not a PDF, DOCX or TXT, or the content doesn't match the extension |
| 413 | File larger than `MAX_UPLOAD_MB` |
| 422 | Empty file, no extractable text (even after OCR, when enabled), damaged file, OCR failed on a page, or an invalid request body |
| 409 | The same file (by content) was already uploaded |
| 503 | `OPENAI_API_KEY` not set, the vector store failed, or tesseract/poppler disappeared while the server was running: retry after fixing |
| 502 | The OpenAI call failed, or the agent didn't finish within `AGENT_MAX_ROUNDS` |

## Demo walkthrough

This is the sample demonstration from the brief, run with the API and UI started as shown above.

1. **Upload.** In the Streamlit sidebar, upload `sample_docs/HR_Policy.pdf`. It appears under
   *Documents* with 3 pages and 4 chunks.
2. **Ask.** In the *Ask documents* tab, type "How many casual leaves are allowed?". The answer is
   **12 days per calendar year**, and *Sources* shows `HR_Policy.pdf — page 2`.
3. **Agent.** In the *Agent chat* tab, type "List my uploaded documents.". The reply lists the
   files, and **Tools used** shows `list_documents`.
4. **Refusal.** Ask something the documents don't cover, e.g. "What is the capital of France?".
   The reply is "I couldn't find this in your uploaded documents." with no sources.
5. **Persistence.** Stop `pka-api` (Ctrl+C), start it again, and repeat step 2. It is still
   answered from the same ChromaDB data in `data/chroma`, with no re-upload.

## Testing

```bash
uv run pytest -q              # the default suite: fast, offline, no API key
uv run pytest -q -m slow      # slow tests: download and use the real MiniLM model
uv run ruff check .
```

Current result: `406 passed, 1 skipped, 3 deselected` for the default suite. The 3 deselected
tests are the `slow` ones; the skipped one is the real-OCR test, which runs only when tesseract
and poppler are installed (see [OCR](#optional-ocr-for-scanned-pdfs)).

What the suite covers, with tests mirroring `src/` under `tests/`:

- **Uploads:** valid PDF/DOCX/TXT are indexed with page and chunk counts. Unsupported or
  mismatched types return 415, oversized files 413, empty or corrupt files 422, duplicates 409. A
  failure mid-ingest leaves no orphan vectors, rows or files.
- **Questions:** a covered question gets an answer with filename/page sources. An unrelated
  question gets the "not found" reply **without the LLM being called**. A missing key returns 503
  and an OpenAI failure 502.
- **OCR:** with OCR on, only pages without a text layer are OCR'd and keep their page numbers;
  with it off, scans get the "needs OCR" 422. Missing packages or programs are logged at startup
  and OCR is turned off; OCR failures during an upload return 422 or 503 and store nothing.
- **Persistence:** chunks written to ChromaDB are found again by a new `PersistentClient` on the
  same folder (the restart scenario).
- **Agent:** "List my uploaded documents" runs `list_documents` and reports it in `tool_calls`.
  A leave question uses `answer_from_documents` and returns its sources. Unknown tools and bad
  JSON arguments are fed back to the model, the step limit returns 502, and history carries over
  within a `session_id`.
- **Source display:** API responses carry structured sources (citation, filename, page, score,
  snippet). The Streamlit rendering itself has no automated tests yet.
- **Retrieval calibration (slow):** with the real MiniLM model, the casual-leave question finds
  the casual-leave chunk, and an unrelated question scores below `MIN_RELEVANCE`.

The tests use a deterministic fake embedder and a scripted fake OpenAI client, on temporary
SQLite and Chroma folders. An autouse fixture clears the app's environment variables and
disables the `.env` file, so tests **never read your `.env` or your API key** and make no
network calls (except the `slow` ones, which download the model).

## Using MySQL

```bash
uv sync --all-packages --extra mysql    # from the repo root (keeps other workspace members)
uv sync --extra mysql                   # from this folder (strips other members, see Setup)
```

Then set the URL in `.env`. The database must already exist; the tables are created at startup:

```
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/pka
```

## Optional: OCR for scanned PDFs

PDF pages that have no text layer (scans, photographed pages) can be read with Tesseract OCR.
Only those pages are OCR'd; pages with embedded text are extracted as usual. OCR'd text keeps the
real page number, so citations such as `scan.pdf, p. 3` stay right.

1. Install the Python packages (`pytesseract`, `pdf2image`) from the repo root:

   ```bash
   uv sync --all-packages --extra ocr
   ```

   (`uv sync --all-packages --all-extras` also installs them, together with the MySQL driver.)

2. Install the OCR programs: Tesseract for recognition, poppler (`pdftoppm`, `pdfinfo`) for
   rendering pages to images:

   ```bash
   brew install tesseract poppler                     # macOS
   sudo apt install tesseract-ocr poppler-utils       # Debian / Ubuntu
   ```

3. Set `OCR_ENABLED=true` in `.env` and restart the API.

At startup the API checks that the packages and the `tesseract`, `pdftoppm` and `pdfinfo`
programs are present. If anything is missing, it logs an `ERROR` that says what to install and
**runs without OCR** rather than refusing to start, since everything else still works. Scanned
PDFs are then rejected with 422 as if OCR were disabled. A startup line
`OCR is enabled for PDF pages that have no text layer.` confirms OCR is active.

Pages are rendered at 300 dpi and read as English (`eng`), with a 120-second limit per page for
rendering and for recognition. If a page can't be rendered or read, the upload fails with 422 and
nothing is stored. If OCR finds no text anywhere, the upload is rejected with 422 ("even with
OCR").

The real-OCR test (`tests/ingestion/test_ocr.py`) is skipped unless `tesseract`, `pdftoppm` and
`pdfinfo` are on `PATH`. All other OCR tests use a fake OCR engine or stubbed libraries.

## Sample documents

`sample_docs/` holds fictional company documents for "Northwind Analytics", generated by
`scripts/make_sample_docs.py`:

| File | Contents |
|---|---|
| `HR_Policy.pdf` | 3 pages. Page 2 has casual (12 days), sick (10) and earned leave side by side, so retrieval has to tell similar rules apart |
| `Employee_Handbook.docx` | Handbook sections (no page numbers, as with all DOCX files) |
| `Travel_Policy.txt` | Plain-text travel policy |

## Screenshots

Captured from a live run and stored in [`docs/screenshots/`](docs/screenshots/):

| File | Shows |
|---|---|
| `01_swagger_endpoints.png` | Swagger UI with all endpoints |
| `02_upload.png` | Uploading `HR_Policy.pdf` and the document list |
| `03_ask_with_sources.png` | Casual-leave answer with its source (HR_Policy.pdf, page 2) |
| `04_unrelated_refusal.png` | Refusal for a question the documents don't cover |
| `05_agent_list_documents.png` | Agent reply with `list_documents` under "Tools used" |
| `06_agent_tools_and_sources.png` | Agent answer showing tools used and sources |

## Project status and limitations

- **No authentication and no multi-user support.** All documents and chat sessions are shared by
  anyone who can reach the API.
- **OCR is optional and basic.** It is off by default. With OCR off, scanned PDFs without a
  text layer are rejected with 422 ("no extractable text"). With OCR on, it runs synchronously
  inside the upload request, so a long scan can take a while (seconds per page) before
  the request returns. It reads English only, applies to PDFs only (not images or DOCX
  files with embedded pictures), and does not straighten skewed or rotated scans. A page that
  has *some* embedded text (e.g. a scan with a typed header) is not OCR'd.
- **No database migrations.** Tables are created with `create_all` at startup. A schema change
  means deleting `data/app.db` (or migrating the database by hand).
- **DOCX and TXT have no page numbers.** Their sources show the filename only (`page: null`).
- No endpoints to delete or re-index documents, and no streaming responses.

## Dependencies

`pyproject.toml` and `uv.lock` are the source of truth; there is no maintained
`requirements.txt`. If one is needed (e.g. for grading), generate it from the lockfile, running
this from this project folder:

```bash
uv export --no-hashes --no-dev --no-emit-project \
  --package ai-personal-knowledge-assistant -o requirements.txt
```

This was verified with uv 0.12.3 from this folder: it writes the pinned runtime dependencies
only. `--no-dev` leaves out pytest, ruff and fpdf2. `--no-emit-project` drops a
`-e ./tasks/ai_personal_knowledge_assistant` line, which is relative to the workspace root and
would break when the file is used from this folder. Install the generated file with
`pip install -r requirements.txt` and then `pip install -e .`. Extras need
`--extra mysql` / `--extra ocr`.

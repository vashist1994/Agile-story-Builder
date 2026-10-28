# Storyforge

Storyforge turns a plain-language product requirement or an uploaded `.txt`, `.pdf`, or `.docx` document into a grounded, traceable Agile backlog with epics, decomposed user stories, Gherkin acceptance criteria, non-functional requirements, assumptions, open questions, coverage, and refinement recommendations. The API uses LangChain with Google Gemini structured output; the React client presents the full backlog and exports it as a spreadsheet-safe CSV.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- A Google Gemini API key

## Run locally

1. Copy `.env.example` to `.env` and set `GOOGLE_API_KEY`.
2. Start the API:

   ```sh
   cd backend
   uv sync
   uv run uvicorn app.main:app --reload
   ```

3. In a second terminal, start the UI:

   ```sh
   cd frontend
   npm install
   npm run dev
   ```

4. Open the Vite URL (normally `http://localhost:5173`). The API docs are at `http://localhost:8000/docs`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | — | Required for story generation. Keep it on the server; never expose it to the browser. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Google Gemini model name. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed browser origins. |
| `MAX_REQUIREMENT_CHARS` | `10000` | Maximum requirement length accepted by the API. |
| `MAX_UPLOAD_BYTES` | `10485760` | Maximum uploaded document size in bytes (10 MiB). |
| `VITE_API_BASE_URL` | `http://localhost:8000` | API origin used by the frontend. |

## Grounding and output

The generation prompt treats the submitted requirement as the sole source of truth. It instructs the model to avoid inventing product behavior, label unresolved details as assumptions, and distinguish explicit exclusions from unspecified scope. Coverage entries receive internal `R-001`-style identifiers; stories refer to those IDs instead of treating generated IDs as source quotations. The API uses Gemini structured output and validates the complete backlog and its cross-references before returning it. Requirements are not persisted.

The CSV endpoint exports the full backlog, with a row for each relevant artifact (summary, epic, story, non-functional requirement, assumption, question, exclusion, coverage entry, and recommendation). Gherkin acceptance criteria are included in each story row. Spreadsheet formula prefixes are neutralized before export.

## API

- `GET /health` — liveness check; does not call Gemini.
- `POST /api/v1/stories/generate` — accepts `{"requirement": "..."}` and returns a validated Agile backlog.
- `POST /api/v1/stories/generate-file` — accepts one `.txt`, `.pdf`, or `.docx` file as multipart form data, extracts its text, and returns a validated Agile backlog.
- `POST /api/v1/stories/export` — accepts the generation response and returns the full backlog as a CSV attachment.

Document uploads are size-limited to 10 MiB by default (`MAX_UPLOAD_BYTES`), parsed on the server, and not persisted. Extracted text is limited by `MAX_REQUIREMENT_CHARS`; scanned/image-only PDFs require OCR before upload. `.txt` files must use UTF-8 encoding, and password-protected PDFs are not supported.

Generation requires a valid API key. Configuration and provider failures are logged server-side and returned as explicit API errors without exposing credentials or provider internals.

## Tests

```sh
cd backend
uv run pytest
```

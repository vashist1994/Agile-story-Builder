# Storyforge

Storyforge turns a plain-language product requirement or an uploaded `.txt`, `.pdf`, or `.docx` document into a grounded, traceable Agile backlog with epics, decomposed user stories, Gherkin acceptance criteria, non-functional requirements, assumptions, open questions, coverage, and refinement recommendations. The API uses LangChain with Google Gemini structured output; the React client presents the full backlog and exports it as a spreadsheet-safe CSV.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- A Google Gemini API key

## Run locally

1. From the project root, copy `.env.example` to `.env` and set `GOOGLE_API_KEY`.
2. Start the FastAPI backend:

   ```sh
   cd backend
   uv sync
   uv run uvicorn app.main:app --reload
   ```

3. Open the interactive FastAPI Swagger UI at **http://127.0.0.1:8000/docs**.
   The alternative ReDoc API reference is at **http://127.0.0.1:8000/redoc**.

The Swagger UI works without starting the React frontend. To run the React UI as well,
start it in a second terminal:

   ```sh
   cd frontend
   npm install
   npm run dev
   ```

Then open the Vite URL (normally `http://localhost:5173`).

## Use the application from Swagger UI

1. Make sure the backend is running and `GOOGLE_API_KEY` is set in the project-root `.env`.
   Restart the backend after changing `.env`.
2. Open **http://127.0.0.1:8000/docs** and expand **Generate backlog**.
3. To enter plain text, open **POST `/api/v1/stories/generate`**, click **Try it out**,
   replace the example `requirement` with your own text (at least 10 characters), and click
   **Execute**. For multi-line requirements, JSON requires line breaks inside the quoted
   string to be written as `\n` (a backslash followed by `n`), not as literal line breaks.
   For example:

   ```json
   {
     "requirement": "A food delivery company wants customers to browse restaurants, order food, make payments, and track deliveries.\nRestaurants manage orders, and delivery partners update delivery status.\nBusiness goal: Improve customer satisfaction and delivery efficiency."
   }
   ```

   In the Swagger request editor, keep the `\n` sequences inside the quoted string exactly
   as shown. Swagger sends them as line breaks in the requirement. If pasted as actual
   newlines within the quotes, the request body is invalid JSON and returns HTTP 422.

4. To upload a requirement, open **POST `/api/v1/stories/generate-file`**, click
   **Try it out**, choose a `.txt`, `.pdf`, or `.docx` file in the `file` control, and click
   **Execute**. The file is parsed on the server; supported document text is sent to the
   same backlog generator.
5. Swagger displays the generated JSON response, including `source_requirement` and
   `backlog`. To download a CSV from Swagger, copy the complete JSON response into
   **POST `/api/v1/stories/export`** as its request body, then click **Execute**. The
   response is a CSV attachment. The React frontend performs this export step for you.

The API key is configured on the backend; do not paste it into Swagger request fields.
Generation can return an error if Gemini is unavailable, the key is invalid, or the model
response does not satisfy the backlog schema. File uploads are limited by `MAX_UPLOAD_BYTES`;
plain text and extracted document text are limited by `MAX_REQUIREMENT_CHARS`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | — | Required for story generation. Keep it on the server; never expose it to the browser. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Google Gemini model name. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed browser origins. |
| `MAX_REQUIREMENT_CHARS` | `10000` | Maximum requirement length accepted by the API. |
| `MAX_UPLOAD_BYTES` | `10485760` | Maximum uploaded document size in bytes (10 MiB). |
| `GEMINI_REQUESTS_PER_MINUTE` | `0` | Optional local sliding-window request cap; `0` disables this cap. Set to a value below the quota assigned to your Google project. |
| `GEMINI_TOKENS_PER_MINUTE` | `0` | Optional local token cap; `0` disables this cap. Requests reserve an approximate prompt-token count plus `GEMINI_MAX_OUTPUT_TOKENS` before calling Gemini and reconcile with returned usage when available. |
| `GEMINI_REQUESTS_PER_DAY` | `0` | Optional local rolling 24-hour request cap; `0` disables this cap. |
| `GEMINI_MAX_CONCURRENCY` | `3` | Maximum concurrent Gemini calls in this process, including corrective validation calls and retries. |
| `GEMINI_RATE_LIMIT_MAX_WAIT_SECONDS` | `30` | Maximum time a request waits for local RPM/TPM/RPD capacity before returning HTTP 429. |
| `GEMINI_MAX_RETRIES` | `2` | Number of bounded retries per Gemini call (maximum 5); only rate limits and transient errors are retried. |
| `GEMINI_RETRY_BASE_SECONDS` | `0.5` | Base exponential-backoff delay, with randomized jitter. |
| `GEMINI_RETRY_MAX_SECONDS` | `15` | Maximum retry delay; provider-specified delays are honored, and a delay above this bound fails gracefully instead of retrying early. |
| `GEMINI_TIMEOUT_SECONDS` | `45` | Timeout for each Gemini call. |
| `GEMINI_MAX_OUTPUT_TOKENS` | `8192` | Output-token limit and conservative TPM reservation; lower it if the configured TPM cap cannot accommodate the default reservation. |
| `GENERATION_CACHE_TTL_SECONDS` | `300` | In-memory cache duration for identical successful requests; `0` disables caching. |
| `GENERATION_CACHE_MAX_ENTRIES` | `256` | Maximum number of cached results; `0` disables caching. |
| `VITE_API_BASE_URL` | `http://localhost:8000` | API origin used by the frontend. |

Quota caps default to disabled because Gemini quotas depend on the Google project,
model, and account tier; configure them using the actual limits shown for your project.
The request coordinator, cache, and request deduplication are process-local. For a
multi-worker or multi-instance deployment, use one worker per quota domain or move
coordination and caching to shared infrastructure before scaling horizontally. Local
limits reduce avoidable bursts but cannot guarantee that Google will never return 429.
Rate-limited calls return HTTP 429, exhausted local/provider quotas do not retry, and
temporary provider failures return HTTP 503 after the configured bounded retries.

## Grounding and output

The generation prompt treats the submitted requirement as the sole source of truth. It instructs the model to avoid inventing product behavior, label unresolved details as assumptions, and distinguish explicit exclusions from unspecified scope. Coverage entries receive internal `R-001`-style identifiers; stories refer to those IDs instead of treating generated IDs as source quotations. The API uses Gemini structured output and validates the complete backlog and its cross-references before returning it. Requirements are not persisted.

The CSV endpoint defaults to an Excel-friendly flat backlog: one row per source requirement, summary, epic, story, non-functional requirement, assumption, open question, out-of-scope item, requirement-coverage entry, and refinement recommendation. Dedicated columns preserve the validated schema fields, including traceability and relationships; acceptance criteria are grouped in each story row with readable Given/When/Then labels and line breaks. Multiple IDs and list values are separated consistently with semicolons, while summary-level values appear only on the summary row. Empty optional values remain blank rather than being replaced with invented content. The UTF-8 CSV includes a BOM for Excel compatibility, uses standard CSV quoting, and neutralizes spreadsheet formula prefixes even when preceded by whitespace or control characters.

The default download is `agile-backlog.csv`. Existing consumers that need the former five-column `Section`, `Item ID`, `Related Item IDs`, `Field`, and `Value` layout can request `POST /api/v1/stories/export?format=normalized`; that response is named `agile-backlog-normalized.csv`.

## API

- `GET /health` — liveness check; does not call Gemini.
- `POST /api/v1/stories/generate` — accepts `{"requirement": "..."}` and returns a validated Agile backlog.
- `POST /api/v1/stories/generate-file` — accepts one `.txt`, `.pdf`, or `.docx` file as multipart form data, extracts its text, and returns a validated Agile backlog.
- `POST /api/v1/stories/export` — accepts the generation response and returns the flat backlog CSV by default; `?format=normalized` selects the optional five-column format.

Document uploads are size-limited to 10 MiB by default (`MAX_UPLOAD_BYTES`), parsed on the server, and not persisted. Extracted text is limited by `MAX_REQUIREMENT_CHARS`; scanned/image-only PDFs require OCR before upload. `.txt` files must use UTF-8 encoding, and password-protected PDFs are not supported.

Generation requires a valid API key. Retries, request latency, retry delays, response token usage, and failures are logged without logging API keys or requirement contents. Configuration and provider failures are returned as explicit API errors without exposing credentials or raw provider responses. Identical concurrent requests share one in-flight generation; successful results are cached in memory for the configured TTL and are never persisted to disk.

## Tests

```sh
cd backend
uv run pytest
```

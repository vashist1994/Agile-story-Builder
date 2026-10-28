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

import csv
import io
import logging
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.documents import (
    DocumentExtractionError,
    RequirementTooLongError,
    extract_requirement_text,
)
from app.schemas import AgileBacklog, GeneratedBacklogResponse, GenerateStoryRequest
from app.service import (
    BacklogValidationError,
    GeminiConfigurationError,
    GeminiProviderError,
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiUnavailableError,
    generate_story,
)

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="Storyforge API",
    description=(
        "Generate grounded Agile backlogs from plain text or uploaded requirement "
        "documents, then export a backlog as CSV."
    ),
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Health",
            "description": "Check whether the API process is responsive.",
        },
        {
            "name": "Generate backlog",
            "description": "Generate a validated Agile backlog from typed text or a document.",
        },
        {
            "name": "Export",
            "description": "Export a generated Agile backlog as a CSV attachment.",
        },
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = exc.errors()
    if any(error["type"] == "json_invalid" for error in errors):
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Invalid JSON request body. Escape line breaks inside the requirement "
                    "string as \\n, or enter the requirement on one line."
                )
            },
        )
    return await request_validation_exception_handler(request, exc)


class ExportStoryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source_requirement: str = Field(min_length=10, max_length=10_000)
    backlog: AgileBacklog


def _spreadsheet_safe(value: str) -> str:
    first_visible = value.lstrip()
    if first_visible.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _format_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _format_criteria(criteria: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"Given {item['given']}\nWhen {item['when']}\nThen {item['then']}" for item in criteria
    )


@app.get(
    "/health",
    tags=["Health"],
    summary="Check API health",
    description="Returns API liveness only; it does not call Gemini.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/stories/generate",
    response_model=GeneratedBacklogResponse,
    tags=["Generate backlog"],
    summary="Generate backlog from text",
    description=(
        "Submit a requirement as JSON. In Swagger UI, click **Try it out**, edit the "
        "example request body, then click **Execute**. JSON line breaks inside the "
        "requirement must be escaped as `\\n`. Requires GOOGLE_API_KEY."
    ),
)
async def create_story(
    payload: GenerateStoryRequest,
) -> GeneratedBacklogResponse:
    return await _generate_backlog_response(payload.requirement)


@app.post(
    "/api/v1/stories/generate-file",
    response_model=GeneratedBacklogResponse,
    tags=["Generate backlog"],
    summary="Generate backlog from a file",
    description=(
        "Upload one UTF-8 TXT, text-based PDF, or DOCX requirement file (maximum 10 MiB "
        "by default). Scanned/image-only PDFs require OCR. Use **Try it out**, choose a "
        "file, then click **Execute**. Requires GOOGLE_API_KEY."
    ),
)
async def create_story_from_file(
    file: Annotated[
        UploadFile,
        File(description="Requirement document. Supported extensions: .txt, .pdf, .docx."),
    ],
) -> GeneratedBacklogResponse:
    filename = file.filename or ""
    try:
        content = await file.read(settings.max_upload_bytes + 1)
    finally:
        await file.close()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB upload limit."
            ),
        )
    try:
        requirement = extract_requirement_text(
            filename=filename,
            content=content,
            max_chars=settings.max_requirement_chars,
        )
    except RequirementTooLongError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _generate_backlog_response(requirement)


async def _generate_backlog_response(requirement: str) -> GeneratedBacklogResponse:
    try:
        backlog = await generate_story(requirement)
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (GeminiQuotaExhaustedError, GeminiRateLimitError) as exc:
        headers = (
            {"Retry-After": str(max(1, int(exc.retry_after)))}
            if exc.retry_after is not None
            else None
        )
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except GeminiUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiProviderError as exc:
        logger.error(
            "Gemini rejected story generation",
            extra={"status_code": exc.status_code, "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BacklogValidationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Story generation failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail="Story generation failed. Please try again.",
        ) from exc
    return GeneratedBacklogResponse(source_requirement=requirement, backlog=backlog)


@app.post(
    "/api/v1/stories/export",
    tags=["Export"],
    summary="Export backlog as CSV",
    description=(
        "Paste the complete JSON response from a successful Generate backlog request "
        "into this request body, then execute to download the CSV file."
    ),
)
async def export_story(payload: ExportStoryRequest) -> StreamingResponse:
    backlog = payload.backlog
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    headers = [
        "Source requirement",
        "Section",
        "ID",
        "Related item IDs",
        "Title",
        "Description",
        "Business value",
        "Persona",
        "User story",
        "Acceptance criteria",
        "Suggested priority",
        "Priority rationale",
        "Refinement status",
        "Requirement references",
        "Assumptions",
        "Open questions",
        "Requirement",
        "Requirement ID",
        "Requirement type",
        "Mapped items",
        "Coverage status",
        "Reason",
        "Testability or clarification",
        "Details",
    ]
    writer.writerow(headers)

    def write_row(section: str, **values: str) -> None:
        row = {header: "" for header in headers}
        row["Source requirement"] = payload.source_requirement
        row["Section"] = section
        row.update(values)
        writer.writerow([_spreadsheet_safe(value) for value in row.values()])

    summary = backlog.requirement_summary
    write_row(
        "Requirement Summary",
        **{
            "Business value": summary.business_objective,
            "Persona": _format_items(summary.actors),
            "Details": (
                f"Key capabilities:\n{_format_items(summary.key_capabilities)}\n\n"
                "Constraints and business rules:\n"
                f"{_format_items(summary.constraints_and_business_rules)}"
            ),
        },
    )

    for epic in backlog.epics:
        write_row(
            "Epic",
            **{
                "ID": epic.epic_id,
                "Related item IDs": _format_items(epic.related_story_ids),
                "Title": epic.title,
                "Description": epic.description,
                "Business value": epic.business_value,
            },
        )

    for story in backlog.user_stories:
        write_row(
            "User Story",
            **{
                "ID": story.story_id,
                "Related item IDs": story.epic_id or "",
                "Title": story.title,
                "Persona": story.persona,
                "User story": story.user_story,
                "Acceptance criteria": _format_criteria(
                    [criterion.model_dump() for criterion in story.acceptance_criteria]
                ),
                "Suggested priority": story.suggested_priority,
                "Priority rationale": story.priority_rationale,
                "Refinement status": story.refinement_status,
                "Requirement references": _format_items(story.requirement_references),
                "Assumptions": _format_items(story.assumptions),
                "Open questions": _format_items(story.open_questions),
                "Details": f"Dependencies:\n{_format_items(story.dependencies)}",
            },
        )

    for item in backlog.non_functional_requirements:
        write_row(
            "Non-Functional Requirement",
            **{
                "Related item IDs": _format_items(item.related_item_ids),
                "ID": item.item_id,
                "Requirement": item.requirement,
                "Testability or clarification": item.testability_or_clarification,
            },
        )

    for assumption in backlog.assumptions:
        write_row("Assumption", Requirement=assumption)

    for question in backlog.open_questions:
        write_row(
            "Open Question",
            **{
                "Requirement": question.question,
                "Related item IDs": _format_items(question.affected_items),
            },
        )

    for item in backlog.out_of_scope:
        write_row("Out of Scope", Requirement=item)

    for coverage in backlog.requirement_coverage:
        write_row(
            "Requirement Coverage",
            **{
                "Requirement": coverage.explicit_requirement,
                "Requirement ID": coverage.requirement_id,
                "Requirement type": coverage.requirement_type,
                "Mapped items": _format_items(coverage.mapped_items),
                "Coverage status": coverage.status,
                "Reason": coverage.reason,
            },
        )

    for recommendation in backlog.refinement_recommendations:
        write_row(
            "Refinement Recommendation",
            **{
                "Requirement": recommendation.recommendation,
                "Related item IDs": _format_items(recommendation.related_items),
            },
        )
    content = io.BytesIO(output.getvalue().encode("utf-8"))
    return StreamingResponse(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="agile-backlog.csv"'},
    )

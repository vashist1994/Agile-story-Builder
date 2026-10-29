import csv
import io
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.documents import (
    DocumentExtractionError,
    RequirementTooLongError,
    extract_requirement_text,
)
from app.schemas import (
    AcceptanceCriterion,
    AgileBacklog,
    GeneratedBacklogResponse,
    GenerateStoryRequest,
)
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
    first_visible = next(
        (
            character
            for character in value
            if not (
                character.isspace()
                or ord(character) < 32
                or 127 <= ord(character) <= 159
                or character == "\ufeff"
            )
        ),
        "",
    )
    if first_visible in {"=", "+", "-", "@"}:
        return f"'{value}"
    return value


FLAT_CSV_HEADERS = [
    "Record Type",
    "ID",
    "Title",
    "Description",
    "Related Item IDs",
    "Source Requirement",
    "Business Objective",
    "Actors",
    "Key Capabilities",
    "Business Rules and Constraints",
    "Business Value",
    "Related Story IDs",
    "Epic ID",
    "Persona",
    "User Story",
    "Acceptance Criteria",
    "Priority",
    "Priority Rationale",
    "Refinement Status",
    "Dependencies",
    "Requirement References",
    "Assumptions",
    "Open Questions",
    "Requirement Text",
    "Related Items",
    "Testability or Clarification",
    "Affected Items",
    "Out-of-Scope Items",
    "Requirement Type",
    "Explicit Requirement",
    "Mapped Backlog Items",
    "Coverage Status",
    "Coverage Reason",
    "Recommendation",
]


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def _format_acceptance_criteria(criteria: list[AcceptanceCriterion]) -> str:
    return "\n\n".join(
        f"Given: {criterion.given}\nWhen: {criterion.when}\nThen: {criterion.then}"
        for criterion in criteria
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
        "into this request body, then execute to download an Excel-friendly flat CSV with "
        "one row per logical backlog item. Use format=normalized for the legacy five-column "
        "field/value layout."
    ),
)
async def export_story(
    payload: ExportStoryRequest,
    export_format: Annotated[
        Literal["flat", "normalized"],
        Query(alias="format", description="CSV layout. Flat is the default."),
    ] = "flat",
) -> StreamingResponse:
    backlog = payload.backlog
    records: list[dict[str, object]] = []

    def add_record(record_type: str, item_id: str = "", **fields: object) -> None:
        record: dict[str, object] = {header: "" for header in FLAT_CSV_HEADERS}
        record["Record Type"] = record_type
        record["ID"] = item_id
        record.update(fields)
        records.append(record)

    summary = backlog.requirement_summary
    add_record(
        "Source Requirement",
        "SOURCE",
        **{"Source Requirement": payload.source_requirement},
    )
    add_record(
        "Requirement Summary",
        "SUMMARY",
        **{
            "Business Objective": summary.business_objective,
            "Actors": summary.actors,
            "Key Capabilities": summary.key_capabilities,
            "Business Rules and Constraints": summary.constraints_and_business_rules,
        },
    )

    for epic in backlog.epics:
        add_record(
            "Epic",
            epic.epic_id,
            Title=epic.title,
            Description=epic.description,
            **{
                "Business Value": epic.business_value,
                "Related Story IDs": epic.related_story_ids,
            },
        )

    for story in backlog.user_stories:
        related_item_ids = ([story.epic_id] if story.epic_id else []) + story.dependencies
        add_record(
            "User Story",
            story.story_id,
            Title=story.title,
            **{
                "Related Item IDs": related_item_ids,
                "Epic ID": story.epic_id,
                "Persona": story.persona,
                "User Story": story.user_story,
                "Acceptance Criteria": _format_acceptance_criteria(story.acceptance_criteria),
                "Priority": story.suggested_priority,
                "Priority Rationale": story.priority_rationale,
                "Refinement Status": story.refinement_status,
                "Dependencies": story.dependencies,
                "Requirement References": story.requirement_references,
                "Assumptions": "\n".join(story.assumptions),
                "Open Questions": "\n".join(story.open_questions),
            },
        )

    for item in backlog.non_functional_requirements:
        add_record(
            "Non-Functional Requirement",
            item.item_id,
            **{
                "Requirement Text": item.requirement,
                "Related Item IDs": item.related_item_ids,
                "Related Items": item.related_item_ids,
                "Testability or Clarification": item.testability_or_clarification,
            },
        )

    for index, assumption in enumerate(backlog.assumptions, start=1):
        add_record("Assumption", f"ASSUMPTION-{index:03}", Assumptions=assumption)

    for index, question in enumerate(backlog.open_questions, start=1):
        add_record(
            "Open Question",
            f"QUESTION-{index:03}",
            **{
                "Open Questions": question.question,
                "Affected Items": question.affected_items,
                "Related Item IDs": question.affected_items,
            },
        )

    for index, item in enumerate(backlog.out_of_scope, start=1):
        add_record(
            "Out of Scope",
            f"OUT-{index:03}",
            **{"Out-of-Scope Items": item},
        )

    for coverage in backlog.requirement_coverage:
        add_record(
            "Requirement Coverage",
            coverage.requirement_id,
            **{
                "Requirement Type": coverage.requirement_type,
                "Explicit Requirement": coverage.explicit_requirement,
                "Mapped Backlog Items": coverage.mapped_items,
                "Related Item IDs": coverage.mapped_items,
                "Coverage Status": coverage.status,
                "Coverage Reason": coverage.reason,
            },
        )

    for index, recommendation in enumerate(backlog.refinement_recommendations, start=1):
        add_record(
            "Refinement Recommendation",
            f"RECOMMENDATION-{index:03}",
            **{
                "Recommendation": recommendation.recommendation,
                "Related Item IDs": recommendation.related_items,
            },
        )

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    if export_format == "flat":
        writer.writerow(FLAT_CSV_HEADERS)
        for record in records:
            writer.writerow(
                [_spreadsheet_safe(_csv_value(record[header])) for header in FLAT_CSV_HEADERS]
            )
        filename = "agile-backlog.csv"
    else:
        normalized_headers = [
            "Section",
            "Item ID",
            "Related Item IDs",
            "Field",
            "Value",
        ]
        writer.writerow(normalized_headers)
        common_headers = {"Record Type", "ID", "Related Item IDs"}
        for record in records:
            for field in FLAT_CSV_HEADERS:
                if field in common_headers or record[field] in ("", None, []):
                    continue
                row = [
                    _csv_value(record["Record Type"]),
                    _csv_value(record["ID"]),
                    _csv_value(record["Related Item IDs"]),
                    field,
                    _csv_value(record[field]),
                ]
                writer.writerow([_spreadsheet_safe(value) for value in row])
        filename = "agile-backlog-normalized.csv"

    content = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return StreamingResponse(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


frontend_static_dir = Path(__file__).resolve().parent / "static"
if frontend_static_dir.is_dir():
    app.mount("/", StaticFiles(directory=frontend_static_dir, html=True), name="frontend")

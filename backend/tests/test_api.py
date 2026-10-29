import asyncio
import csv
import io
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from app.documents import DocumentExtractionError, extract_requirement_text
from app.main import app
from app.schemas import AgileBacklog, NonFunctionalRequirement
from app.service import (
    BacklogValidationError,
    GeminiRateLimitError,
    GeminiUnavailableError,
    generate_story,
)

client = TestClient(app)


def pdf_with_text(text: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(pdf)


def example_backlog() -> AgileBacklog:
    return AgileBacklog.model_validate(
        {
            "requirement_summary": {
                "business_objective": "Let users export a report.",
                "actors": ["user"],
                "key_capabilities": ["Export reports"],
                "constraints_and_business_rules": [],
            },
            "epics": [
                {
                    "epic_id": "E1",
                    "title": "Report export",
                    "description": "Export reports.",
                    "business_value": "Share reports.",
                    "related_story_ids": ["S1"],
                }
            ],
            "user_stories": [
                {
                    "story_id": "S1",
                    "epic_id": "E1",
                    "title": '=HYPERLINK("https://example.test")',
                    "persona": "user",
                    "user_story": "As a user, I want to export a report, so that I can share it.",
                    "acceptance_criteria": [
                        {
                            "given": "a report is available",
                            "when": "the user exports it",
                            "then": "a CSV file is downloaded",
                        }
                    ],
                    "suggested_priority": "Not suggested",
                    "priority_rationale": "The requirement does not specify priority.",
                    "dependencies": [],
                    "refinement_status": "Ready for Refinement Review",
                    "requirement_references": ["R-001"],
                    "assumptions": [],
                    "open_questions": ["Which report formats are required?"],
                }
            ],
            "non_functional_requirements": [
                {
                    "item_id": "NFR-001",
                    "requirement": "The export should be fast.",
                    "related_item_ids": ["S1"],
                    "testability_or_clarification": "No measurable threshold is specified.",
                }
            ],
            "assumptions": ["Unconfirmed: user means an authorized report user."],
            "open_questions": [
                {"question": "Which report formats are required?", "affected_items": ["S1"]}
            ],
            "out_of_scope": [],
            "requirement_coverage": [
                {
                    "requirement_id": "R-001",
                    "requirement_type": "Functional",
                    "explicit_requirement": "export a report",
                    "mapped_items": ["S1"],
                    "status": "Covered",
                    "reason": "S1 covers report export.",
                },
                {
                    "requirement_id": "R-002",
                    "requirement_type": "Non-functional",
                    "explicit_requirement": "The export should be fast.",
                    "mapped_items": ["NFR-001"],
                    "status": "Partially covered",
                    "reason": "A measurable performance threshold is not specified.",
                },
            ],
            "refinement_recommendations": [
                {"recommendation": "Confirm the supported report formats.", "related_items": ["S1"]}
            ],
        }
    )


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_swagger_openapi_documents_text_and_file_generation() -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    text_operation = paths["/api/v1/stories/generate"]["post"]
    file_operation = paths["/api/v1/stories/generate-file"]["post"]
    export_operation = paths["/api/v1/stories/export"]["post"]

    assert text_operation["tags"] == ["Generate backlog"]
    assert text_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"]["GenerateStoryRequest"]
    assert request_schema["examples"][0]["requirement"]
    assert "\\n" in request_schema["examples"][0]["requirement"]
    assert file_operation["tags"] == ["Generate backlog"]
    assert "multipart/form-data" in file_operation["requestBody"]["content"]
    file_schema = schema["components"]["schemas"][
        "Body_create_story_from_file_api_v1_stories_generate_file_post"
    ]
    assert "Supported extensions" in file_schema["properties"]["file"]["description"]
    assert export_operation["tags"] == ["Export"]
    assert "complete JSON response" in export_operation["description"]


def test_generation_preflight_allows_local_vite_origins() -> None:
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        response = client.options(
            "/api/v1/stories/generate",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_generation_returns_validated_backlog(monkeypatch) -> None:
    async def fake_generate(requirement: str) -> AgileBacklog:
        assert requirement == "Users can export reports as CSV files."
        return example_backlog()

    monkeypatch.setattr("app.main.generate_story", fake_generate)
    response = client.post(
        "/api/v1/stories/generate",
        json={"requirement": "Users can export reports as CSV files."},
    )
    assert response.status_code == 200
    assert response.json()["backlog"]["user_stories"][0]["story_id"] == "S1"
    assert response.json()["backlog"]["requirement_summary"]["business_objective"]
    assert response.json()["source_requirement"] == "Users can export reports as CSV files."


def test_generation_retries_invalid_references_once(monkeypatch) -> None:
    invalid_data = example_backlog().model_dump()
    invalid_data["user_stories"][0]["requirement_references"] = ["R-009"]
    with pytest.raises(ValidationError) as validation_error:
        AgileBacklog.model_validate(invalid_data)

    responses = [
        {"raw": None, "parsed": None, "parsing_error": validation_error.value},
        {"raw": None, "parsed": example_backlog(), "parsing_error": None},
    ]

    class FakeModel:
        def with_structured_output(self, _schema, **kwargs):
            assert kwargs == {"method": "json_schema", "include_raw": True}
            return RunnableLambda(lambda _input: responses.pop(0))

    monkeypatch.setattr(
        "app.service.get_settings",
        lambda: SimpleNamespace(google_api_key="test-key", gemini_model="test-model"),
    )
    monkeypatch.setattr("app.service.ChatGoogleGenerativeAI", lambda **_kwargs: FakeModel())

    result = asyncio.run(generate_story("A valid product requirement for backlog generation."))

    assert result == example_backlog()
    assert responses == []


def test_generation_raises_clear_error_after_invalid_retry(monkeypatch) -> None:
    invalid_data = example_backlog().model_dump()
    invalid_data["user_stories"][0]["requirement_references"] = ["R-009"]
    with pytest.raises(ValidationError) as validation_error:
        AgileBacklog.model_validate(invalid_data)

    responses = [
        {"raw": None, "parsed": None, "parsing_error": validation_error.value},
        {"raw": None, "parsed": None, "parsing_error": validation_error.value},
    ]

    class FakeModel:
        def with_structured_output(self, _schema, **_kwargs):
            return RunnableLambda(lambda _input: responses.pop(0))

    monkeypatch.setattr(
        "app.service.get_settings",
        lambda: SimpleNamespace(google_api_key="test-key", gemini_model="test-model"),
    )
    monkeypatch.setattr("app.service.ChatGoogleGenerativeAI", lambda **_kwargs: FakeModel())

    with pytest.raises(BacklogValidationError, match="after one automatic correction attempt"):
        asyncio.run(generate_story("A valid product requirement for backlog generation."))
    assert responses == []


def test_generation_returns_actionable_error_if_corrective_retry_fails(monkeypatch) -> None:
    async def fail_validation(_requirement: str) -> AgileBacklog:
        raise BacklogValidationError(
            "Gemini returned an inconsistent backlog after one automatic correction attempt. "
            "Please retry the request."
        )

    monkeypatch.setattr("app.main.generate_story", fail_validation)
    response = client.post(
        "/api/v1/stories/generate",
        json={"requirement": "A valid product requirement for backlog generation."},
    )

    assert response.status_code == 502
    assert "after one automatic correction attempt" in response.json()["detail"]


def test_generation_returns_rate_limit_status_and_retry_after(monkeypatch) -> None:
    async def fail_rate_limit(_requirement: str) -> AgileBacklog:
        raise GeminiRateLimitError("Gemini rate limit is temporarily unavailable.", retry_after=7)

    monkeypatch.setattr("app.main.generate_story", fail_rate_limit)
    response = client.post(
        "/api/v1/stories/generate",
        json={"requirement": "A valid product requirement for backlog generation."},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert "rate limit" in response.json()["detail"]


def test_generation_returns_service_unavailable_after_transient_failures(monkeypatch) -> None:
    async def fail_transient(_requirement: str) -> AgileBacklog:
        raise GeminiUnavailableError("Gemini is temporarily unavailable.")

    monkeypatch.setattr("app.main.generate_story", fail_transient)
    response = client.post(
        "/api/v1/stories/generate",
        json={"requirement": "A valid product requirement for backlog generation."},
    )

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]


def test_multiline_swagger_requirement_accepts_escaped_json_newlines(monkeypatch) -> None:
    requirement = (
        "A food delivery company wants customers to browse restaurants, order food, make "
        "payments, and track deliveries.\n"
        "Restaurants manage orders, and delivery partners update delivery status.\n"
        "Business goal: Improve customer satisfaction and delivery efficiency."
    )

    async def fake_generate(value: str) -> AgileBacklog:
        assert value == requirement
        return example_backlog()

    monkeypatch.setattr("app.main.generate_story", fake_generate)
    response = client.post("/api/v1/stories/generate", json={"requirement": requirement})

    assert response.status_code == 200
    assert response.json()["source_requirement"] == requirement


def test_generation_explains_unescaped_newline_in_json_string() -> None:
    raw_json = '{"requirement": "Line one\nLine two, with enough text."}'
    response = client.post(
        "/api/v1/stories/generate",
        content=raw_json,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Invalid JSON request body. Escape line breaks inside the requirement "
        "string as \\n, or enter the requirement on one line."
    )


def test_generation_rejects_short_requirement() -> None:
    response = client.post("/api/v1/stories/generate", json={"requirement": "short"})
    assert response.status_code == 422


def test_export_returns_full_backlog_csv_and_neutralizes_formulas() -> None:
    response = client.post(
        "/api/v1/stories/export",
        json={
            "source_requirement": '=IMPORTDATA("https://example.test")',
            "backlog": example_backlog().model_dump(),
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="agile-backlog.csv"' == response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["Section"] == "Requirement Summary"
    assert rows[0]["Source requirement"].startswith("'=")
    story_row = next(row for row in rows if row["Section"] == "User Story")
    assert story_row["Title"].startswith("'=")
    expected_criteria = (
        "Given a report is available\nWhen the user exports it\nThen a CSV file is downloaded"
    )
    assert story_row["Acceptance criteria"] == expected_criteria
    assert story_row["Requirement references"] == "'- R-001"
    assert story_row["Open questions"] == "'- Which report formats are required?"
    coverage_row = next(row for row in rows if row["Section"] == "Requirement Coverage")
    assert coverage_row["Requirement ID"] == "R-001"
    assert coverage_row["Requirement type"] == "Functional"
    nfr_row = next(row for row in rows if row["Section"] == "Non-Functional Requirement")
    assert nfr_row["ID"] == "NFR-001"
    assert nfr_row["Requirement ID"] == ""
    assert {row["Section"] for row in rows} >= {
        "Epic",
        "User Story",
        "Assumption",
        "Open Question",
        "Requirement Coverage",
        "Refinement Recommendation",
    }


def test_backlog_rejects_dangling_requirement_reference() -> None:
    data = example_backlog().model_dump()
    data["user_stories"][0]["requirement_references"] = ["R-999"]
    with pytest.raises(ValueError, match="unknown IDs: R-999"):
        AgileBacklog.model_validate(data)


def test_backlog_repairs_epic_story_membership_from_story_epic_id() -> None:
    data = example_backlog().model_dump()
    data["epics"][0]["related_story_ids"] = []

    backlog = AgileBacklog.model_validate(data)

    assert backlog.user_stories[0].epic_id == "E1"
    assert backlog.epics[0].related_story_ids == ["S1"]


def test_backlog_rejects_story_with_unknown_epic() -> None:
    data = example_backlog().model_dump()
    data["user_stories"][0]["epic_id"] = "E9"
    data["epics"][0]["related_story_ids"] = []

    with pytest.raises(ValueError, match="S1 references unknown epic E9"):
        AgileBacklog.model_validate(data)


def test_non_functional_item_does_not_duplicate_requirement_id() -> None:
    item = {
        "item_id": "NFR-001",
        "requirement": "Export response has a stated performance target.",
        "related_item_ids": ["S1"],
        "testability_or_clarification": "Define a measurable threshold.",
    }

    parsed = NonFunctionalRequirement.model_validate({**item, "requirement_id": "R-NFR-01"})

    assert parsed.item_id == "NFR-001"
    assert "requirement_id" not in parsed.model_dump()


def test_backlog_ignores_legacy_nfr_requirement_id() -> None:
    data = example_backlog().model_dump()
    data["non_functional_requirements"][0]["requirement_id"] = "R-NFR-01"

    backlog = AgileBacklog.model_validate(data)

    assert backlog.non_functional_requirements[0].item_id == "NFR-001"
    assert "requirement_id" not in backlog.non_functional_requirements[0].model_dump()


def test_generate_from_txt_upload(monkeypatch) -> None:
    requirement = "Users can export reports as CSV files."

    async def fake_generate(value: str) -> AgileBacklog:
        assert value == requirement
        return example_backlog()

    monkeypatch.setattr("app.main.generate_story", fake_generate)
    response = client.post(
        "/api/v1/stories/generate-file",
        files={"file": ("requirements.txt", requirement.encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["source_requirement"] == requirement


def test_generate_file_rejects_upload_over_configured_limit(monkeypatch) -> None:
    monkeypatch.setattr("app.main.settings.max_upload_bytes", 16)
    response = client.post(
        "/api/v1/stories/generate-file",
        files={
            "file": ("requirements.txt", b"this file exceeds the configured limit", "text/plain")
        },
    )
    assert response.status_code == 413
    assert "upload limit" in response.json()["detail"]


def test_generate_from_docx_upload(monkeypatch) -> None:
    buffer = io.BytesIO()
    document = Document()
    document.add_paragraph("Users can export reports as CSV files.")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Reports include their title."
    document.save(buffer)
    expected = "Users can export reports as CSV files.\nReports include their title."

    async def fake_generate(value: str) -> AgileBacklog:
        assert value == expected
        return example_backlog()

    monkeypatch.setattr("app.main.generate_story", fake_generate)
    response = client.post(
        "/api/v1/stories/generate-file",
        files={
            "file": (
                "requirements.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["source_requirement"] == expected


def test_generate_from_pdf_upload(monkeypatch) -> None:
    requirement = "Users can export reports as CSV files."

    async def fake_generate(value: str) -> AgileBacklog:
        assert value == requirement
        return example_backlog()

    monkeypatch.setattr("app.main.generate_story", fake_generate)
    response = client.post(
        "/api/v1/stories/generate-file",
        files={"file": ("requirements.pdf", pdf_with_text(requirement), "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["source_requirement"] == requirement


@pytest.mark.parametrize(
    ("filename", "content", "error"),
    [
        ("requirements.exe", b"plain text requirement", "Unsupported file type"),
        ("requirements.txt", b"\xff\xfe", "UTF-8"),
        ("requirements.pdf", b"not a pdf document", "not a valid PDF"),
        ("requirements.docx", b"not a word document", "Could not read the DOCX"),
    ],
)
def test_reject_invalid_uploads(filename: str, content: bytes, error: str) -> None:
    response = client.post(
        "/api/v1/stories/generate-file",
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 400
    assert error in response.json()["detail"]


def test_extract_requirement_rejects_empty_text_and_oversize_content() -> None:
    with pytest.raises(DocumentExtractionError, match="at least 10 characters"):
        extract_requirement_text("requirements.txt", b"short", 100)
    with pytest.raises(ValueError, match="exceeds the 10-character limit"):
        extract_requirement_text("requirements.txt", b"long requirement", 10)


def test_extract_pdf_rejects_image_only_document(monkeypatch) -> None:
    class ImageOnlyPdf:
        is_encrypted = False
        pages: list[object] = []

    monkeypatch.setattr("app.documents.PdfReader", lambda *_args, **_kwargs: ImageOnlyPdf())
    with pytest.raises(DocumentExtractionError, match="No readable text"):
        extract_requirement_text("requirements.pdf", b"%PDF-1.7", 10_000)

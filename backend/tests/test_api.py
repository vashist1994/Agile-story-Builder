import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AgileBacklog

client = TestClient(app)


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
            "non_functional_requirements": [],
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
                }
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

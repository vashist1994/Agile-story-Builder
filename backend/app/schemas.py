from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateStoryRequest(StrictModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    requirement: str = Field(min_length=10, max_length=10_000)


class RequirementSummary(StrictModel):
    business_objective: str
    actors: list[str]
    key_capabilities: list[str]
    constraints_and_business_rules: list[str]


class Epic(StrictModel):
    epic_id: str = Field(description="Sequential identifier such as E1.")
    title: str
    description: str
    business_value: str
    related_story_ids: list[str] = Field(description="Story identifiers belonging to this epic.")


class AcceptanceCriterion(StrictModel):
    given: str
    when: str
    then: str


class UserStory(StrictModel):
    story_id: str = Field(description="Sequential identifier such as S1.")
    epic_id: str | None = Field(
        description="Related epic identifier, or null when an epic hierarchy is unnecessary."
    )
    title: str
    persona: str
    user_story: str = Field(
        description='A statement in the format "As a..., I want..., so that...".'
    )
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    suggested_priority: Literal["High", "Medium", "Low", "Not suggested"]
    priority_rationale: str
    dependencies: list[str] = Field(description="Identifiers of prerequisite stories, if any.")
    refinement_status: Literal[
        "Draft",
        "Needs Clarification",
        "Ready for Refinement Review",
        "Sprint-Ready Candidate",
    ]
    requirement_references: list[str] = Field(
        min_length=1,
        description="Internal requirement IDs such as R-001, not generated source quotations.",
    )
    assumptions: list[str] = Field(
        description="Story-specific interpretations; label each as unconfirmed."
    )
    open_questions: list[str] = Field(
        description="Precise unresolved stakeholder questions affecting this story."
    )


class NonFunctionalRequirement(StrictModel):
    item_id: str = Field(description="Sequential identifier such as NFR-001.")
    requirement_id: str = Field(description="Internal requirement inventory ID such as R-001.")
    requirement: str
    related_item_ids: list[str]
    testability_or_clarification: str


class OpenQuestion(StrictModel):
    question: str
    affected_items: list[str]


class RequirementCoverage(StrictModel):
    requirement_id: str = Field(
        description="Sequential internal inventory identifier such as R-001."
    )
    requirement_type: Literal[
        "Functional",
        "Non-functional",
        "Business rule",
        "Constraint",
        "Actor goal",
        "Business objective",
        "Other",
    ]
    explicit_requirement: str
    mapped_items: list[str]
    status: Literal["Covered", "Partially covered", "Not covered"]
    reason: str


class RefinementRecommendation(StrictModel):
    recommendation: str
    related_items: list[str]


class AgileBacklog(StrictModel):
    requirement_summary: RequirementSummary
    epics: list[Epic]
    user_stories: list[UserStory]
    non_functional_requirements: list[NonFunctionalRequirement]
    assumptions: list[str]
    open_questions: list[OpenQuestion]
    out_of_scope: list[str]
    requirement_coverage: list[RequirementCoverage]
    refinement_recommendations: list[RefinementRecommendation]

    @model_validator(mode="after")
    def validate_backlog_references(self) -> "AgileBacklog":
        epic_ids = [epic.epic_id for epic in self.epics]
        story_ids = [story.story_id for story in self.user_stories]
        nfr_ids = [item.item_id for item in self.non_functional_requirements]
        requirement_ids = [item.requirement_id for item in self.requirement_coverage]

        self._validate_sequence(epic_ids, "E")
        self._validate_sequence(story_ids, "S")
        self._validate_sequence(nfr_ids, "NFR-")
        self._validate_sequence(requirement_ids, "R-")

        epic_id_set = set(epic_ids)
        story_id_set = set(story_ids)
        requirement_id_set = set(requirement_ids)
        backlog_item_ids = epic_id_set | story_id_set | set(nfr_ids)

        for story in self.user_stories:
            if story.epic_id is not None and story.epic_id not in epic_id_set:
                raise ValueError(f"{story.story_id} references unknown epic {story.epic_id}.")

        stories_by_epic = {
            epic.epic_id: [
                story.story_id for story in self.user_stories if story.epic_id == epic.epic_id
            ]
            for epic in self.epics
        }
        for epic in self.epics:
            # story.epic_id is the canonical relationship; this list duplicates it.
            epic.related_story_ids = stories_by_epic[epic.epic_id]
        for story in self.user_stories:
            self._validate_references(
                story.requirement_references, requirement_id_set, story.story_id
            )
            self._validate_references(story.dependencies, story_id_set, story.story_id)
            if story.story_id in story.dependencies:
                raise ValueError(f"{story.story_id} cannot depend on itself.")
        for item in self.non_functional_requirements:
            if item.requirement_id not in requirement_id_set:
                raise ValueError(
                    f"{item.item_id} references unknown requirement {item.requirement_id}."
                )
            self._validate_references(item.related_item_ids, backlog_item_ids, item.item_id)
        for coverage in self.requirement_coverage:
            self._validate_references(
                coverage.mapped_items, backlog_item_ids, coverage.requirement_id
            )

        for question in self.open_questions:
            self._validate_references(
                question.affected_items, backlog_item_ids | requirement_id_set, "open question"
            )
        for recommendation in self.refinement_recommendations:
            self._validate_references(
                recommendation.related_items,
                backlog_item_ids | requirement_id_set,
                "refinement recommendation",
            )
        self._validate_dependency_cycles()
        return self

    def _validate_dependency_cycles(self) -> None:
        dependencies = {story.story_id: story.dependencies for story in self.user_stories}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(story_id: str) -> None:
            if story_id in visiting:
                raise ValueError("Story dependencies must not contain a cycle.")
            if story_id in visited:
                return
            visiting.add(story_id)
            for dependency in dependencies[story_id]:
                visit(dependency)
            visiting.remove(story_id)
            visited.add(story_id)

        for story_id in dependencies:
            visit(story_id)

    @staticmethod
    def _validate_sequence(values: list[str], prefix: str) -> None:
        expected = [
            f"{prefix}{index:03d}" if prefix.endswith("-") else f"{prefix}{index}"
            for index in range(1, len(values) + 1)
        ]
        if values != expected:
            raise ValueError(f"IDs with prefix {prefix!r} must be unique and sequential.")

    @staticmethod
    def _validate_references(references: list[str], valid_ids: set[str], owner: str) -> None:
        unknown = set(references) - valid_ids
        if unknown:
            raise ValueError(f"{owner} references unknown IDs: {', '.join(sorted(unknown))}.")


class GeneratedBacklogResponse(StrictModel):
    source_requirement: str
    backlog: AgileBacklog

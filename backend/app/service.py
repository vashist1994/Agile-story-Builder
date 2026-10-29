import logging

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.prompts import STORY_PROMPT
from app.schemas import AgileBacklog

logger = logging.getLogger(__name__)


class GeminiConfigurationError(RuntimeError):
    pass


class BacklogValidationError(RuntimeError):
    pass


VALIDATION_RETRY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            """Your previous backlog response failed schema/reference validation:
{validation_error}

Regenerate the complete backlog from the original requirement. Correct the validation
issues without changing or inventing product facts. Before responding, ensure:
- Every story requirement_references value exactly matches an R-ID in requirement_coverage.
- Requirement IDs are unique and sequential, and all referenced IDs exist.
- All epic, story, NFR, dependency, and mapped-item references resolve to the right IDs.
- The entire response matches the declared schema.
Return only the structured backlog.""",
        )
    ]
)


async def generate_story(requirement: str) -> AgileBacklog:
    settings = get_settings()
    if not settings.google_api_key:
        raise GeminiConfigurationError("Google Gemini is not configured. Set GOOGLE_API_KEY.")

    model = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=0,
        max_retries=2,
        timeout=45,
    )
    structured_model = model.with_structured_output(
        AgileBacklog,
        method="json_schema",
        include_raw=True,
    )
    initial_chain = STORY_PROMPT | structured_model
    result = await initial_chain.ainvoke({"requirement": requirement})
    parsed = result["parsed"]
    if isinstance(parsed, AgileBacklog):
        return parsed

    validation_error = result["parsing_error"]
    if validation_error is None:
        raise BacklogValidationError(
            "Gemini did not return a backlog matching the expected schema."
        )

    logger.warning(
        "Generated backlog failed schema/reference validation; retrying once: %s",
        validation_error,
    )
    retry_chain = STORY_PROMPT + VALIDATION_RETRY_PROMPT | structured_model
    retry_result = await retry_chain.ainvoke(
        {
            "requirement": requirement,
            "validation_error": str(validation_error),
        }
    )
    retry_parsed = retry_result["parsed"]
    if isinstance(retry_parsed, AgileBacklog):
        return retry_parsed

    retry_error = retry_result["parsing_error"]
    logger.error(
        "Generated backlog remained invalid after corrective retry: %s",
        retry_error,
    )
    raise BacklogValidationError(
        "Gemini returned an inconsistent backlog after one automatic correction attempt. "
        "Please retry the request."
    ) from retry_error

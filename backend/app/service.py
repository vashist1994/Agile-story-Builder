from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.prompts import STORY_PROMPT
from app.schemas import AgileBacklog


class GeminiConfigurationError(RuntimeError):
    pass


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
    chain = STORY_PROMPT | model.with_structured_output(AgileBacklog, method="json_schema")
    return await chain.ainvoke({"requirement": requirement})

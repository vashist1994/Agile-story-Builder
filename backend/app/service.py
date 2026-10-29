import asyncio
import hashlib
import json
import logging
import random
import re
import time
import weakref
from collections import OrderedDict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.gemini_limits import (
    GeminiRateLimiter,
    LocalQuotaExceededError,
    estimate_tokens,
)
from app.prompts import STORY_PROMPT
from app.schemas import AgileBacklog

logger = logging.getLogger(__name__)
_RUNTIME_LOCK = Lock()
_RUNTIMES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, tuple[tuple[Any, ...], "GenerationRuntime"]
] = weakref.WeakKeyDictionary()


class GeminiConfigurationError(RuntimeError):
    pass


class BacklogValidationError(RuntimeError):
    pass


class GeminiProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class GeminiRateLimitError(GeminiProviderError):
    pass


class GeminiQuotaExhaustedError(GeminiProviderError):
    pass


class GeminiUnavailableError(GeminiProviderError):
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


class GenerationRuntime:
    def __init__(self, settings: Any) -> None:
        self.limiter = GeminiRateLimiter(
            requests_per_minute=_setting(settings, "gemini_requests_per_minute", 0),
            tokens_per_minute=_setting(settings, "gemini_tokens_per_minute", 0),
            requests_per_day=_setting(settings, "gemini_requests_per_day", 0),
            max_concurrency=_setting(settings, "gemini_max_concurrency", 3),
            max_wait_seconds=_setting(settings, "gemini_rate_limit_max_wait_seconds", 30),
        )
        self.cache_ttl = _setting(settings, "generation_cache_ttl_seconds", 300)
        self.cache_max_entries = _setting(settings, "generation_cache_max_entries", 256)
        self.cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.in_flight: dict[str, asyncio.Task[AgileBacklog]] = {}
        self.lock = asyncio.Lock()


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _runtime_settings_key(settings: Any) -> tuple[Any, ...]:
    defaults = {
        "gemini_requests_per_minute": 0,
        "gemini_tokens_per_minute": 0,
        "gemini_requests_per_day": 0,
        "gemini_max_concurrency": 3,
        "gemini_rate_limit_max_wait_seconds": 30,
        "gemini_max_retries": 2,
        "gemini_retry_base_seconds": 0.5,
        "gemini_retry_max_seconds": 15,
        "gemini_timeout_seconds": 45,
        "gemini_max_output_tokens": 8192,
        "generation_cache_ttl_seconds": 300,
        "generation_cache_max_entries": 256,
    }
    return tuple(_setting(settings, name, default) for name, default in defaults.items())


def _get_runtime(settings: Any) -> GenerationRuntime:
    loop = asyncio.get_running_loop()
    key = _runtime_settings_key(settings)
    with _RUNTIME_LOCK:
        current = _RUNTIMES.get(loop)
        if current is None or current[0] != key:
            runtime = GenerationRuntime(settings)
            _RUNTIMES[loop] = key, runtime
            return runtime
        return current[1]


def _cache_key(requirement: str, model_name: str) -> str:
    data = {
        "requirement": requirement,
        "model": model_name,
        "prompt": repr(STORY_PROMPT.messages),
        "schema": AgileBacklog.model_json_schema(),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


async def generate_story(requirement: str) -> AgileBacklog:
    settings = get_settings()
    if not settings.google_api_key:
        raise GeminiConfigurationError("Google Gemini is not configured. Set GOOGLE_API_KEY.")

    runtime = _get_runtime(settings)
    key = _cache_key(requirement, settings.gemini_model)
    async with runtime.lock:
        now = time.monotonic()
        cached = runtime.cache.get(key)
        if cached is not None:
            expires_at, data = cached
            if expires_at > now:
                runtime.cache.move_to_end(key)
                logger.info(
                    "Gemini generation cache hit",
                    extra={"model": settings.gemini_model, "cache_hit": True},
                )
                return AgileBacklog.model_validate(data)
            del runtime.cache[key]

        task = runtime.in_flight.get(key)
        if task is None:
            task = asyncio.create_task(_generate_and_cache(requirement, key, settings, runtime))
            runtime.in_flight[key] = task
    return await asyncio.shield(task)


async def _generate_and_cache(
    requirement: str,
    key: str,
    settings: Any,
    runtime: GenerationRuntime,
) -> AgileBacklog:
    try:
        backlog = await _generate_uncached(requirement, settings, runtime)
        if runtime.cache_ttl and runtime.cache_max_entries:
            async with runtime.lock:
                runtime.cache[key] = (
                    time.monotonic() + runtime.cache_ttl,
                    backlog.model_dump(mode="json"),
                )
                runtime.cache.move_to_end(key)
                while len(runtime.cache) > runtime.cache_max_entries:
                    runtime.cache.popitem(last=False)
        return backlog
    finally:
        async with runtime.lock:
            runtime.in_flight.pop(key, None)


async def _generate_uncached(
    requirement: str,
    settings: Any,
    runtime: GenerationRuntime,
) -> AgileBacklog:
    timeout = _setting(settings, "gemini_timeout_seconds", 45)
    model = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=0,
        max_retries=0,
        timeout=timeout,
        max_output_tokens=_setting(settings, "gemini_max_output_tokens", 8192),
    )
    structured_model = model.with_structured_output(
        AgileBacklog,
        method="json_schema",
        include_raw=True,
    )
    initial_chain = STORY_PROMPT | structured_model
    initial_prompt = STORY_PROMPT.format(requirement=requirement)
    result = await _invoke_with_retries(
        initial_chain,
        {"requirement": requirement},
        initial_prompt,
        runtime,
        settings,
    )
    parsed = result["parsed"]
    if isinstance(parsed, AgileBacklog):
        return parsed

    validation_error = result["parsing_error"]
    if validation_error is None:
        raise BacklogValidationError(
            "Gemini did not return a backlog matching the expected schema."
        )

    logger.warning(
        "Generated backlog failed schema/reference validation; requesting one correction",
        extra={"error_type": type(validation_error).__name__},
    )
    retry_chain = STORY_PROMPT + VALIDATION_RETRY_PROMPT | structured_model
    retry_prompt = STORY_PROMPT.format(requirement=requirement) + VALIDATION_RETRY_PROMPT.format(
        validation_error=str(validation_error)
    )
    retry_result = await _invoke_with_retries(
        retry_chain,
        {
            "requirement": requirement,
            "validation_error": str(validation_error),
        },
        retry_prompt,
        runtime,
        settings,
    )
    retry_parsed = retry_result["parsed"]
    if isinstance(retry_parsed, AgileBacklog):
        return retry_parsed

    retry_error = retry_result["parsing_error"]
    logger.error(
        "Generated backlog remained invalid after corrective retry",
        extra={"error_type": type(retry_error).__name__ if retry_error else "Unknown"},
    )
    raise BacklogValidationError(
        "Gemini returned an inconsistent backlog after one automatic correction attempt. "
        "Please retry the request."
    ) from retry_error


async def _invoke_with_retries(
    chain: Any,
    payload: dict[str, str],
    prompt_text: str,
    runtime: GenerationRuntime,
    settings: Any,
) -> dict[str, Any]:
    max_retries = _setting(settings, "gemini_max_retries", 2)
    base_delay = _setting(settings, "gemini_retry_base_seconds", 0.5)
    max_delay = _setting(settings, "gemini_retry_max_seconds", 15)
    token_reserve = _setting(settings, "gemini_max_output_tokens", 8192)
    request_tokens = estimate_tokens(prompt_text, token_reserve)

    for attempt in range(max_retries + 1):
        started = time.monotonic()
        result: dict[str, Any] | None = None
        request_error: Exception | None = None
        try:
            async with runtime.limiter.request_slot(request_tokens) as reservation:
                started = time.monotonic()
                try:
                    result = await chain.ainvoke(payload)
                except Exception as exc:
                    request_error = exc
                if result is not None:
                    usage = _token_usage(result)
                    if usage is not None:
                        await runtime.limiter.record_actual_tokens(reservation, usage[0])
        except LocalQuotaExceededError as exc:
            raise GeminiQuotaExhaustedError(str(exc), status_code=429) from exc

        if request_error is not None:
            exc = request_error
            retryable, category, status_code, provider_delay = _classify_provider_error(exc)
            if category == "quota":
                logger.error(
                    "Gemini quota exhausted",
                    extra={
                        "model": settings.gemini_model,
                        "status_code": status_code,
                        "error_type": type(exc).__name__,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                    },
                )
                raise GeminiQuotaExhaustedError(
                    "Gemini quota is exhausted. Check the quota assigned to your API project.",
                    status_code=status_code,
                ) from exc
            if not retryable:
                logger.error(
                    "Gemini rejected request",
                    extra={
                        "model": settings.gemini_model,
                        "status_code": status_code,
                        "error_type": type(exc).__name__,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                    },
                )
                raise GeminiProviderError(
                    f"Gemini rejected the request ({category}). Check the server configuration "
                    "and requirement, then try again.",
                    status_code=status_code,
                ) from exc

            if attempt >= max_retries:
                error_type = (
                    GeminiRateLimitError if category == "rate_limit" else GeminiUnavailableError
                )
                message = (
                    "Gemini rate limit is temporarily unavailable. Please retry later."
                    if category == "rate_limit"
                    else "Gemini is temporarily unavailable after retrying. Please try again later."
                )
                logger.error(
                    "Gemini request retries exhausted",
                    extra={
                        "model": settings.gemini_model,
                        "attempts": attempt + 1,
                        "status_code": status_code,
                        "error_type": type(exc).__name__,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                    },
                )
                raise error_type(
                    message,
                    status_code=status_code,
                    retry_after=provider_delay,
                ) from exc

            if provider_delay is not None and provider_delay > max_delay:
                error_type = (
                    GeminiRateLimitError if category == "rate_limit" else GeminiUnavailableError
                )
                message = (
                    "Gemini rate limit is temporarily unavailable. Please retry later."
                    if category == "rate_limit"
                    else "Gemini is temporarily unavailable. Please try again later."
                )
                raise error_type(
                    message,
                    status_code=status_code,
                    retry_after=provider_delay,
                ) from exc

            exponential_delay = min(max_delay, base_delay * (2**attempt))
            delay = max(random.uniform(0, exponential_delay), provider_delay or 0)
            logger.warning(
                "Retrying Gemini request",
                extra={
                    "model": settings.gemini_model,
                    "attempt": attempt + 1,
                    "next_attempt": attempt + 2,
                    "status_code": status_code,
                    "error_type": type(exc).__name__,
                    "retry_delay_seconds": round(delay, 3),
                },
            )
            await asyncio.sleep(delay)
        elif result is not None:
            usage = _token_usage(result)
            logger.info(
                "Gemini request completed",
                extra={
                    "model": settings.gemini_model,
                    "attempts": attempt + 1,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "input_tokens": usage[1] if usage else None,
                    "output_tokens": usage[2] if usage else None,
                    "total_tokens": usage[0] if usage else None,
                    "cache_hit": False,
                },
            )
            return result
        else:
            raise GeminiProviderError("Gemini returned no structured response.")

    raise AssertionError("Retry loop exited without returning or raising.")


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _classify_provider_error(
    error: BaseException,
) -> tuple[bool, str, int | None, float | None]:
    chain = _exception_chain(error)
    status_code: int | None = None
    text_parts: list[str] = []
    class_names: list[str] = []
    provider_delay: float | None = None

    for item in chain:
        class_names.append(type(item).__name__.lower())
        text_parts.extend(
            str(value)
            for value in (getattr(item, "message", None), getattr(item, "details", None))
            if value is not None
        )
        for attr in ("status_code", "code"):
            code = getattr(item, attr, None)
            if isinstance(code, int):
                status_code = code
                break
        if provider_delay is None:
            provider_delay = _provider_retry_delay(item)

    text = " ".join(text_parts).lower()
    names = " ".join(class_names)
    if status_code in (403, 429) and _is_quota_exhausted(text):
        return False, "quota", status_code, provider_delay
    if status_code == 429 or "ratelimit" in names or "rate_limit" in names:
        return True, "rate_limit", status_code or 429, provider_delay
    if status_code in (408, 500, 502, 503, 504):
        return True, "transient", status_code, provider_delay
    if status_code is not None:
        if status_code in (401, 403):
            return False, "authentication or permission failure", status_code, None
        if 400 <= status_code < 500:
            return False, "invalid request", status_code, None

    transient_names = (
        "timeoutexception",
        "connecterror",
        "connecttimeout",
        "writetimeout",
        "pooltimeout",
        "readtimeout",
        "readerror",
        "writeerror",
        "closeerror",
        "proxyerror",
        "networkerror",
        "remoteprotocolerror",
    )
    if any(name in names for name in transient_names) or isinstance(
        error, (TimeoutError, ConnectionError, OSError)
    ):
        return True, "transient", status_code, provider_delay
    return False, "provider error", status_code, None


def _is_quota_exhausted(text: str) -> bool:
    compact_text = re.sub(r"[\W_]+", "", text)
    if any(
        marker in text
        for marker in (
            "per day",
            "per-day",
            "daily quota",
            "current quota",
            "requests_per_day",
            "rpd",
            "billing not enabled",
            "billing details",
            "check your plan",
            "quota exhausted",
            "quota has been exhausted",
            "exceeded your quota",
        )
    ):
        return True
    if any(
        marker in compact_text
        for marker in (
            "requestsperday",
            "tokensperday",
            "requestsper24hours",
            "billingnotenabled",
        )
    ):
        return True
    if any(
        marker in compact_text
        for marker in ("requestsperminute", "tokensperminute", "requestspersecond")
    ):
        return False
    return "quota exceeded" in text and not any(
        marker in text
        for marker in (
            "per minute",
            "per-minute",
            "requests_per_minute",
            "tokens_per_minute",
            "rpm",
            "tpm",
        )
    )


def _provider_retry_delay(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return max(0, float(retry_after))
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    return max(0, (retry_at - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass

    details = getattr(error, "details", None)
    retry_value = _find_retry_delay(details)
    if retry_value is None:
        return None
    try:
        if isinstance(retry_value, (int, float)):
            return max(0, float(retry_value))
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", str(retry_value))
        return max(0, float(match.group(1))) if match else None
    except (TypeError, ValueError):
        return None


def _find_retry_delay(value: Any) -> Any | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in ("retrydelay", "retry_delay"):
                return child
            found = _find_retry_delay(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_retry_delay(child)
            if found is not None:
                return found
    return None


def _token_usage(result: Any) -> tuple[int, int, int] | None:
    raw = result.get("raw") if isinstance(result, dict) else None
    metadata = getattr(raw, "usage_metadata", None)
    if not isinstance(metadata, dict):
        return None
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    total_tokens = metadata.get("total_tokens")
    if not all(isinstance(value, int) and value >= 0 for value in (input_tokens, output_tokens)):
        return None
    total = (
        total_tokens
        if isinstance(total_tokens, int) and total_tokens >= 0
        else (input_tokens + output_tokens)
    )
    return total, input_tokens, output_tokens

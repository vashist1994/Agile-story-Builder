import asyncio
from types import SimpleNamespace

import pytest

from app.gemini_limits import GeminiRateLimiter, LocalQuotaExceededError
from app.service import (
    GeminiProviderError,
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiUnavailableError,
    GenerationRuntime,
    _invoke_with_retries,
    generate_story,
)
from tests.test_api import example_backlog


class ProviderError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = {"error": {"message": message}}
        self.response = SimpleNamespace(headers={"Retry-After": retry_after} if retry_after else {})


def test_rpm_limit_wait_is_bounded() -> None:
    async def run() -> None:
        limiter = GeminiRateLimiter(
            requests_per_minute=1,
            tokens_per_minute=0,
            requests_per_day=0,
            max_concurrency=1,
            max_wait_seconds=0.01,
        )
        async with limiter.request_slot(1):
            pass
        with pytest.raises(LocalQuotaExceededError, match="quota is currently exhausted"):
            async with limiter.request_slot(1):
                pytest.fail("A request above the configured RPM cap was admitted.")

    asyncio.run(run())


def test_rpd_limit_rejects_calls_after_the_configured_daily_cap() -> None:
    async def run() -> None:
        limiter = GeminiRateLimiter(
            requests_per_minute=0,
            tokens_per_minute=0,
            requests_per_day=1,
            max_concurrency=1,
            max_wait_seconds=0,
        )
        async with limiter.request_slot(1):
            pass
        with pytest.raises(LocalQuotaExceededError, match="quota is currently exhausted"):
            async with limiter.request_slot(1):
                pytest.fail("A request above the configured RPD cap was admitted.")

    asyncio.run(run())


def test_tpm_rejects_a_request_larger_than_the_configured_quota() -> None:
    async def run() -> None:
        limiter = GeminiRateLimiter(
            requests_per_minute=0,
            tokens_per_minute=10,
            requests_per_day=0,
            max_concurrency=1,
            max_wait_seconds=0,
        )
        with pytest.raises(LocalQuotaExceededError, match="estimated request exceeds"):
            async with limiter.request_slot(11):
                pytest.fail("An oversized request was admitted.")

    asyncio.run(run())


def test_concurrency_limit_covers_the_provider_call() -> None:
    async def run() -> None:
        limiter = GeminiRateLimiter(
            requests_per_minute=0,
            tokens_per_minute=0,
            requests_per_day=0,
            max_concurrency=1,
            max_wait_seconds=1,
        )
        active = 0
        peak = 0

        async def make_request() -> None:
            nonlocal active, peak
            async with limiter.request_slot(1):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(make_request(), make_request(), make_request())
        assert peak == 1

    asyncio.run(run())


def test_rate_limit_retry_honors_retry_after_and_logs_token_usage(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr("app.service.random.uniform", lambda _start, _end: 0.1)
        settings = SimpleNamespace(
            gemini_model="test-model",
            gemini_max_retries=1,
            gemini_retry_base_seconds=0.2,
            gemini_retry_max_seconds=5,
            gemini_max_output_tokens=1,
        )
        runtime = GenerationRuntime(settings)
        attempts = 0

        class Chain:
            async def ainvoke(self, _payload):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ProviderError(429, "requests per minute rate limit", retry_after="2")
                raw = SimpleNamespace(
                    usage_metadata={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
                )
                return {"raw": raw, "parsed": object(), "parsing_error": None}

        result = await _invoke_with_retries(
            Chain(),
            {"requirement": "Private requirement"},
            "Prompt",
            runtime,
            settings,
        )
        assert result["parsed"] is not None
        assert attempts == 2
        assert recorded == [2]
        assert runtime.limiter._usage[-1].tokens == 20

    async def async_sleep(delay: float) -> None:
        # Capture retry waits without stalling the focused test.
        recorded.append(delay)

    recorded: list[float] = []
    monkeypatch.setattr("app.service.asyncio.sleep", async_sleep)
    asyncio.run(run())


@pytest.mark.parametrize(
    ("error", "expected_exception"),
    [
        (ProviderError(401, "invalid API key"), GeminiProviderError),
        (ProviderError(400, "invalid request"), GeminiProviderError),
        (ProviderError(429, "daily quota exhausted"), GeminiQuotaExhaustedError),
        (ProviderError(403, "You exceeded your current quota."), GeminiQuotaExhaustedError),
    ],
)
def test_non_retryable_provider_failures_are_not_retried(
    error: Exception,
    expected_exception: type[Exception],
) -> None:
    async def run() -> None:
        settings = SimpleNamespace(
            gemini_model="test-model",
            gemini_max_retries=3,
            gemini_retry_base_seconds=0.01,
            gemini_retry_max_seconds=1,
            gemini_max_output_tokens=1,
        )
        runtime = GenerationRuntime(settings)
        attempts = 0

        class Chain:
            async def ainvoke(self, _payload):
                nonlocal attempts
                attempts += 1
                raise error

        with pytest.raises(expected_exception):
            await _invoke_with_retries(Chain(), {}, "Prompt", runtime, settings)
        assert attempts == 1

    asyncio.run(run())


def test_retry_limit_returns_rate_limit_error_without_extra_attempts(monkeypatch) -> None:
    async def run() -> None:
        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("app.service.asyncio.sleep", no_sleep)
        settings = SimpleNamespace(
            gemini_model="test-model",
            gemini_max_retries=1,
            gemini_retry_base_seconds=0.001,
            gemini_retry_max_seconds=0.001,
            gemini_max_output_tokens=1,
        )
        runtime = GenerationRuntime(settings)
        attempts = 0

        class Chain:
            async def ainvoke(self, _payload):
                nonlocal attempts
                attempts += 1
                raise ProviderError(429, "rate limit")

        with pytest.raises(GeminiRateLimitError):
            await _invoke_with_retries(Chain(), {}, "Prompt", runtime, settings)
        assert attempts == 2

    asyncio.run(run())


def test_transient_server_error_is_retried_with_a_bounded_attempt_count(monkeypatch) -> None:
    async def run() -> None:
        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("app.service.asyncio.sleep", no_sleep)
        settings = SimpleNamespace(
            gemini_model="test-model",
            gemini_max_retries=1,
            gemini_retry_base_seconds=0.001,
            gemini_retry_max_seconds=0.001,
            gemini_max_output_tokens=1,
        )
        runtime = GenerationRuntime(settings)
        attempts = 0

        class Chain:
            async def ainvoke(self, _payload):
                nonlocal attempts
                attempts += 1
                raise ProviderError(503, "service unavailable")

        with pytest.raises(GeminiUnavailableError):
            await _invoke_with_retries(Chain(), {}, "Prompt", runtime, settings)
        assert attempts == 2

    asyncio.run(run())


def test_identical_concurrent_and_subsequent_requests_are_deduplicated(monkeypatch) -> None:
    async def run() -> None:
        backlog = example_backlog()
        calls = 0

        class FakeModel:
            def with_structured_output(self, _schema, **_kwargs):
                async def invoke(_payload):
                    nonlocal calls
                    calls += 1
                    await asyncio.sleep(0.01)
                    return {"raw": None, "parsed": backlog, "parsing_error": None}

                from langchain_core.runnables import RunnableLambda

                return RunnableLambda(invoke)

        monkeypatch.setattr(
            "app.service.get_settings",
            lambda: SimpleNamespace(
                google_api_key="not-logged",
                gemini_model="test-model",
                gemini_max_retries=0,
            ),
        )
        monkeypatch.setattr("app.service.ChatGoogleGenerativeAI", lambda **_kwargs: FakeModel())
        requirement = "Same requirement for concurrent request deduplication."
        first, second = await asyncio.gather(
            generate_story(requirement),
            generate_story(requirement),
        )
        third = await generate_story(requirement)
        assert first == backlog
        assert second == backlog
        assert third == backlog
        assert calls == 1

    asyncio.run(run())

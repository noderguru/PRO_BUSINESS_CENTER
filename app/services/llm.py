"""Єдина точка в коді, що знає про OpenAI SDK.

Назовні віддає власний DTO, тому зміна провайдера не розтікається по сервісах.
"""

import logging
import time
from dataclasses import dataclass

import openai
from tenacity import (
    RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter,
)

from app.config import Settings
from app.errors import LLMConfigError, LLMRateLimitedError, LLMUnavailableError
from app.services.pricing import Usage

log = logging.getLogger(__name__)

# ретраї лише на тимчасові збої; на 4xx-валідації повторів немає
RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: Usage
    response_id: str | None
    latency_ms: int


def _log_retry(state: RetryCallState) -> None:
    log.warning("llm retry attempt=%s after=%r", state.attempt_number, state.outcome.exception())


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = openai.OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=settings.openai_timeout_seconds,
            max_retries=0,  # ретраями керує tenacity, щоб політика була в одному місці
        )

    def chat(self, model: str, messages: list[dict]) -> LLMResponse:
        started = time.monotonic()
        try:
            completion = self._call(model, messages)
        except openai.RateLimitError as exc:
            raise LLMRateLimitedError(retry_after=_retry_after(exc)) from exc
        except openai.AuthenticationError as exc:
            log.error("llm auth failed: %s", exc)  # деталі лише в лог
            raise LLMConfigError() from exc
        except openai.PermissionDeniedError as exc:
            log.error("llm permission denied: %s", exc)
            raise LLMConfigError() from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise LLMUnavailableError() from exc
        except openai.APIStatusError as exc:
            log.error("llm status error status=%s", exc.status_code)
            raise LLMUnavailableError() from exc

        return LLMResponse(
            content=(completion.choices[0].message.content or ""),
            usage=_normalize_usage(completion),
            response_id=getattr(completion, "id", None),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),  # перша спроба + максимум 2 повтори
        wait=wait_exponential_jitter(initial=0.5, max=8),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _call(self, model: str, messages: list[dict]):
        return self._client.chat.completions.create(model=model, messages=messages)


def _retry_after(exc: openai.RateLimitError) -> int | None:
    value = (getattr(exc, "response", None) and exc.response.headers.get("retry-after")) or None
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def _normalize_usage(completion) -> Usage:
    usage = getattr(completion, "usage", None)
    if usage is None:
        # провайдер не віддав usage — краще нуль у явному вигляді, ніж вигадані токени
        log.warning("provider returned no usage block")
        return Usage(prompt_tokens=0, completion_tokens=0, cached_prompt_tokens=0)

    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    return Usage(
        prompt_tokens=usage.prompt_tokens or 0,
        completion_tokens=usage.completion_tokens or 0,
        cached_prompt_tokens=cached,
    )

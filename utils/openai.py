"""OpenAI-compatible API via the official openai library. Implements llm_api interface."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from loguru import logger
from openai import OpenAI

# Exception types (openai >=1.0)
try:
    from openai import (
        APIError,
        APIConnectionError,
        APITimeoutError,
        BadRequestError,
        RateLimitError,
    )
except (ImportError, AttributeError):
    APIError = APIConnectionError = APITimeoutError = RateLimitError = BadRequestError = None  # type: ignore


class ModelErrorType(StrEnum):
    TOKEN_LIMIT = "token_limit"
    EMPTY_INPUT = "empty_input"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    BAD_REQUEST = "bad_request"
    TRANSIENT_API = "transient_api"
    INVALID_RESPONSE = "invalid_response"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class ModelError:
    error_type: ModelErrorType
    message: str
    retryable: bool
    exceed: bool = False


def _new_result(model: str) -> dict[str, Any]:
    return {
        "content": None,
        "reasoning_content": None,
        "usage": None,
        "response_time": None,
        "model": model,
        "exceed": False,
        "error_type": None,
        "error_message": None,
        "retryable": False,
    }


def _apply_error_result(result: dict[str, Any], error: ModelError) -> dict[str, Any]:
    result["exceed"] = error.exceed
    result["error_type"] = error.error_type.value
    result["error_message"] = error.message
    result["retryable"] = error.retryable
    return result


def _dump_usage(usage_obj: Any) -> dict[str, Any] | None:
    if usage_obj is None:
        return None
    if hasattr(usage_obj, "model_dump"):
        return usage_obj.model_dump()
    return {
        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
        "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
    }


def _looks_like_token_limit(msg: str) -> bool:
    """Heuristics (HTTP 400 body checks) for prompt/context overruns."""
    lower = msg.lower()
    return (
        "input prompt contain" in lower
        or "context_length_exceeded" in lower
        or "maximum context" in lower
        or "this model's maximum context" in lower
        or "requested too many tokens" in lower
    )


def _looks_like_empty_input_length(msg: str) -> bool:
    """Detect provider validation errors caused by empty input messages."""
    lower = msg.lower()
    return "range of input length should be [1," in lower


def _looks_like_payload_too_large(e: BaseException) -> bool:
    """413 / gateway bad_response_status_code: retrying the same request will not help."""
    text = str(e).lower()
    if "bad_response_status_code" in text:
        return True
    if "error code: 413" in text or "code: 413" in text:
        return True
    if APIError is not None and isinstance(e, APIError) and getattr(e, "status_code", None) == 413:
        return True
    return False


def _classify_openai_error(e: BaseException) -> ModelError:
    err_text = str(e)
    if BadRequestError is not None and isinstance(e, BadRequestError):
        if _looks_like_token_limit(err_text):
            return ModelError(ModelErrorType.TOKEN_LIMIT, err_text, retryable=False, exceed=True)
        if _looks_like_empty_input_length(err_text):
            return ModelError(ModelErrorType.EMPTY_INPUT, err_text, retryable=False)
        if _looks_like_payload_too_large(e):
            return ModelError(ModelErrorType.PAYLOAD_TOO_LARGE, err_text, retryable=False)
        return ModelError(ModelErrorType.BAD_REQUEST, err_text, retryable=True)

    if RateLimitError is not None and isinstance(e, RateLimitError):
        return ModelError(ModelErrorType.TRANSIENT_API, err_text, retryable=True)
    if APIConnectionError is not None and isinstance(e, APIConnectionError):
        return ModelError(ModelErrorType.TRANSIENT_API, err_text, retryable=True)
    if APITimeoutError is not None and isinstance(e, APITimeoutError):
        return ModelError(ModelErrorType.TRANSIENT_API, err_text, retryable=True)
    if APIError is not None and isinstance(e, APIError):
        if _looks_like_payload_too_large(e):
            return ModelError(ModelErrorType.PAYLOAD_TOO_LARGE, err_text, retryable=False)
        return ModelError(ModelErrorType.TRANSIENT_API, err_text, retryable=True)

    return ModelError(ModelErrorType.UNEXPECTED, err_text, retryable=False)


def _retry_delay(base_delay: float, attempt: int) -> float:
    return base_delay * (2**attempt) + random.uniform(0, 1)


def _log_model_error(error: ModelError, model: str) -> None:
    message = f"{error.error_type.value}: {error.message} - {model}"
    if error.error_type == ModelErrorType.UNEXPECTED:
        logger.exception(message)
    elif error.retryable:
        logger.error(message)
    else:
        logger.warning(message)


def _consume_chat_stream(stream: Any) -> tuple[str, str | None, dict[str, Any] | None, str | None]:
    """Aggregate streaming chat completion chunks into (content, reasoning, usage, resolved_model)."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] | None = None
    resolved_model: str | None = None
    for chunk in stream:
        if getattr(chunk, "model", None):
            resolved_model = chunk.model
        u = getattr(chunk, "usage", None)
        if u is not None:
            usage = _dump_usage(u)
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if not delta:
            continue
        piece = getattr(delta, "content", None) or ""
        if piece:
            content_parts.append(piece)
        rc = getattr(delta, "reasoning_content", None) or ""
        if rc:
            reasoning_parts.append(rc)
    reasoning: str | None = "".join(reasoning_parts) if reasoning_parts else None
    return "".join(content_parts), reasoning, usage, resolved_model


def call_model(
    token: str,
    api_address: str,
    model: str = "gpt-4o-mini",
    message: str = "Why can't I see the light?",
    max_retries: int = 5,
    base_delay: float = 1.0,
    temperature: float = 0.0,
    timeout: float = 300.0,
    api_path: str = "/v1/chat/completions",
    stream: bool | None = None,
) -> dict[str, Any]:
    """Call chat completions (OpenAI SDK) with retry/backoff."""

    When ``stream`` is True (or left as default and ``model`` is ``MiniMax-M2.7-highspeed``), uses the
    streaming API and concatenates delta text into ``content`` / ``reasoning_content``.
    """
    _ = api_path
    do_stream = stream if stream is not None else (model == "MiniMax-M2.7-highspeed")
    result = _new_result(model)
    client = OpenAI(
        api_key=token or "sk-placeholder",
        base_url=api_address,
        max_retries=0,
    )

    for attempt in range(max_retries):
        try:
            t0 = time.time()
            if do_stream:
                completion_stream = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": message}],
                    timeout=timeout,
                    temperature=temperature,
                    stream=True,
                )
                content, reasoning, usage, resolved = _consume_chat_stream(completion_stream)
                response_time = round(time.time() - t0, 2)
                if not content and not reasoning:
                    error = ModelError(
                        ModelErrorType.INVALID_RESPONSE,
                        "Invalid stream response: empty content",
                        retryable=False,
                    )
                    _log_model_error(error, model)
                    return _apply_error_result(result, error)
                result["content"] = content
                result["reasoning_content"] = reasoning
                result["usage"] = usage
                result["response_time"] = response_time
                result["model"] = resolved or model
                logger.debug(
                    f"Call model {result['model']} successfully (stream) in {response_time}s, usage={usage}"
                )
                return result

            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": message}],
                timeout=timeout,
                temperature=temperature,
                stream=False,
            )
            response_time = round(time.time() - t0, 2)
            choice = (resp.choices or [None])[0]
            if not choice or not getattr(choice, "message", None):
                error = ModelError(
                    ModelErrorType.INVALID_RESPONSE,
                    f"Invalid response: missing choices, id={getattr(resp, 'id', '?')}",
                    retryable=False,
                )
                _log_model_error(error, model)
                return _apply_error_result(result, error)

            msg_obj = choice.message
            content = (msg_obj.content or "") if msg_obj else ""
            reasoning = getattr(msg_obj, "reasoning_content", None) if msg_obj else None

            usage = _dump_usage(resp.usage)

            result["content"] = content
            result["reasoning_content"] = reasoning
            result["usage"] = usage
            result["response_time"] = response_time
            result["model"] = getattr(resp, "model", None) or model
            logger.debug(
                f"Call model {result['model']} successfully in {response_time}s, usage={usage}"
            )
            return result

        except Exception as e:
            error = _classify_openai_error(e)
            if error.retryable and attempt < max_retries - 1:
                delay = _retry_delay(base_delay, attempt)
                logger.warning(
                    f"{error.error_type.value} ({error.message}), retrying in {delay:.2f}s... (attempt {attempt + 1}/{max_retries} - {model})"
                )
                time.sleep(delay)
                continue

            _log_model_error(error, model)
            return _apply_error_result(result, error)

    return result


def get_models(token: str, api_address: str) -> list | None:
    """List available models. For openai-compatible APIs."""
    try:
        client = OpenAI(api_key=token or "sk-placeholder", base_url=api_address)
        models = client.models.list()
        if not models.data:
            return []
        return [m.model_dump() if hasattr(m, "model_dump") else {"id": getattr(m, "id", str(m))} for m in models.data]
    except Exception as e:
        logger.warning(f"get_models failed: {e}")
        return None


def llm_output_to_json(output: str | None) -> dict[str, Any] | None:
    """Parse LLM output string to JSON dict."""
    if output is None or not isinstance(output, str):
        return None
    s = output.strip().replace("\n", "")
    if not s:
        return None
    if s.startswith("```json"):
        s = s[7:].strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        logger.debug(f"llm_output_to_json parse failed: {e}")
        return None

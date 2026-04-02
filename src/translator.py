from __future__ import annotations

import json
import random
import time

import requests

from src.config import AppConfig
from src.models import Chunk, TranslatedChunk


class RetryableHttpError(RuntimeError):
    pass


class NonRetryableHttpError(RuntimeError):
    pass


def preflight_live_api(config: AppConfig) -> tuple[bool, str]:
    """Run a minimal request to validate auth/model/base_url before chunk loop."""
    probe = Chunk(
        chunk_id="preflight",
        page_number=0,
        order=0,
        chunk_type="text",
        source_text="hello",
    )
    result = _translate_live(probe, config)
    if result.success:
        return True, ""
    return False, result.error or "Unknown API preflight failure."


def translate_chunks(chunks: list[Chunk], config: AppConfig) -> list[TranslatedChunk]:
    if config.use_mock_translator:
        return [_mock_translate_chunk(c, config.target_lang) for c in chunks]

    translated: list[TranslatedChunk] = []
    for chunk in chunks:
        translated.append(_translate_with_timeout_fallback(chunk, config))
    return translated


def _mock_translate_chunk(chunk: Chunk, target_lang: str) -> TranslatedChunk:
    return TranslatedChunk(
        chunk_id=chunk.chunk_id,
        translated_text=f"[{target_lang}] {chunk.source_text}",
        success=True,
    )


def _translate_live(chunk: Chunk, config: AppConfig) -> TranslatedChunk:
    prompt = (
        f"Translate from {config.source_lang} to {config.target_lang}. "
        "Return translation only. Do not explain.\n\n"
        f"Text:\n{chunk.source_text}"
    )
    endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model_name,
        "messages": [
            {"role": "system", "content": "You are a professional translator."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    attempts = config.retry_count + 1
    for idx in range(attempts):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=(config.connect_timeout_sec, config.read_timeout_sec),
            )
            if response.status_code >= 400:
                message = _extract_http_error(response)
                if _is_retryable_status(response.status_code):
                    raise RetryableHttpError(f"HTTP {response.status_code}: {message}")
                raise NonRetryableHttpError(f"HTTP {response.status_code}: {message}")

            data = response.json()
            content = _extract_message_content(data)
            if not content:
                raise RuntimeError("Empty translation content in API response.")
            return TranslatedChunk(chunk_id=chunk.chunk_id, translated_text=content, success=True)
        except NonRetryableHttpError as exc:
            return TranslatedChunk(
                chunk_id=chunk.chunk_id,
                translated_text="",
                success=False,
                error=str(exc),
            )
        except Exception as exc:
            if idx == attempts - 1 or not _is_retryable_exception(exc):
                return TranslatedChunk(
                    chunk_id=chunk.chunk_id,
                    translated_text="",
                    success=False,
                    error=str(exc),
                )
            time.sleep(_backoff_seconds(idx))

    return TranslatedChunk(chunk_id=chunk.chunk_id, translated_text="", success=False, error="Unknown error")


def _translate_with_timeout_fallback(
    chunk: Chunk,
    config: AppConfig,
    depth: int = 0,
) -> TranslatedChunk:
    """Retry large timed-out chunks by splitting into smaller pieces."""
    result = _translate_live(chunk, config)
    if result.success:
        return result

    error_text = (result.error or "").lower()
    should_split = (
        "timed out" in error_text
        or "timeout" in error_text
        or "connection reset by peer" in error_text
        or "connection aborted" in error_text
    ) and len(chunk.source_text) > 220
    if not should_split or depth >= 2:
        return result

    left_text, right_text = _split_text_half(chunk.source_text)
    left_chunk = Chunk(
        chunk_id=f"{chunk.chunk_id}_a",
        page_number=chunk.page_number,
        order=chunk.order,
        chunk_type=chunk.chunk_type,
        source_text=left_text,
        table_id=chunk.table_id,
        row_index=chunk.row_index,
        col_index=chunk.col_index,
    )
    right_chunk = Chunk(
        chunk_id=f"{chunk.chunk_id}_b",
        page_number=chunk.page_number,
        order=chunk.order,
        chunk_type=chunk.chunk_type,
        source_text=right_text,
        table_id=chunk.table_id,
        row_index=chunk.row_index,
        col_index=chunk.col_index,
    )

    left_result = _translate_with_timeout_fallback(left_chunk, config, depth + 1)
    right_result = _translate_with_timeout_fallback(right_chunk, config, depth + 1)

    if left_result.success and right_result.success:
        merged = f"{left_result.translated_text}\n{right_result.translated_text}".strip()
        return TranslatedChunk(chunk_id=chunk.chunk_id, translated_text=merged, success=True)

    merged_err = (
        "Timeout fallback failed: "
        f"left=({left_result.error or 'ok'}), "
        f"right=({right_result.error or 'ok'})"
    )
    return TranslatedChunk(chunk_id=chunk.chunk_id, translated_text="", success=False, error=merged_err)


def _split_text_half(text: str) -> tuple[str, str]:
    mid = len(text) // 2
    # Prefer sentence boundary near the midpoint to keep context readable.
    window_start = max(0, mid - 120)
    window_end = min(len(text), mid + 120)
    window = text[window_start:window_end]
    pivot = window.rfind(". ")
    if pivot != -1:
        split_idx = window_start + pivot + 1
    else:
        split_idx = mid
    left = text[:split_idx].strip()
    right = text[split_idx:].strip()
    if not left or not right:
        # Guaranteed non-empty halves.
        split_idx = max(1, mid)
        left = text[:split_idx].strip()
        right = text[split_idx:].strip()
    return left, right


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 429} or status_code >= 500


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, (RetryableHttpError, requests.Timeout, requests.ConnectionError)):
        return True
    msg = str(exc).lower()
    transient_markers = [
        "connection reset by peer",
        "connection aborted",
        "temporarily unavailable",
        "timed out",
        "timeout",
        "broken pipe",
        "remote end closed connection",
    ]
    return any(marker in msg for marker in transient_markers)


def _backoff_seconds(retry_idx: int) -> float:
    base = min(20.0, 1.5 * (2 ** retry_idx))
    return base + random.uniform(0.0, 0.6)


def _extract_http_error(response: requests.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return response.text.strip()[:800] or "No response body."

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or str(err)
            code = err.get("code")
            if code:
                return f"{msg} (code={code})"
            return str(msg)
        if "message" in data:
            return str(data["message"])
    return str(data)[:800]


def _extract_message_content(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()

    # Some OpenAI-compatible providers return content as a list of typed blocks.
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                pieces.append(str(item.get("text", "")))
        return "\n".join(pieces).strip()

    return str(content).strip()

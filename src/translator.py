"""Gemini translation: prompt construction, chunking, retry, and error mapping.

Nothing here imports Streamlit, so the whole module is testable with a fake client and no
network. `translate` is the only function the UI needs.
"""

from __future__ import annotations

import time
from typing import Protocol

from google import genai
from google.genai import errors, types

from .config import (
    FALLBACK_MODELS,
    MAX_INPUT_CHARS,
    MAX_RETRIES,
    MODEL_NAME,
    RETRY_BASE_DELAY,
    TRANSLATION_CHUNK_CHARS,
    get_api_key,
)

_SYSTEM_INSTRUCTION = (
    "You are a professional translator. Translate the user's text into {target}.\n"
    "Rules:\n"
    "- Output ONLY the translation. No preamble, no notes, no explanation, no quotes "
    "around the result.\n"
    "- Preserve the original line breaks, paragraph structure, and any ' | ' column "
    "separators exactly.\n"
    "- Keep numbers, dates, URLs, email addresses, and proper nouns intact unless the "
    "target language has a standard rendering of them.\n"
    "- Translate meaning, not word-for-word. The result must read naturally to a native "
    "speaker.\n"
    "- If a passage is already in {target}, return it unchanged."
)

#: HTTP codes worth retrying: rate limit, then transient server-side failures.
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})

#: Never sleep longer than this on a server-suggested retry, so the UI cannot appear hung.
_MAX_RETRY_SLEEP = 45.0


def _error_details(exc: Exception) -> list:
    """The google.rpc detail entries attached to an APIError, if any."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        error = details.get("error", details)
        if isinstance(error, dict):
            return error.get("details", []) or []
    return []


def _daily_quota_exhausted(exc: Exception) -> bool:
    """True when a 429 is the per-day free-tier cap rather than a per-minute burst.

    The distinction decides everything: a per-minute limit clears in under a minute, so
    sleeping is right. The per-day cap does not clear until the quota window rolls over,
    so sleeping is useless — but the cap is per *model*, so switching models does work.
    """
    for entry in _error_details(exc):
        if not isinstance(entry, dict) or "QuotaFailure" not in str(entry.get("@type", "")):
            continue
        for violation in entry.get("violations", []) or []:
            if "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def _retry_delay(exc: Exception, attempt: int) -> float:
    """How long to wait before the next attempt.

    Gemini returns a google.rpc.RetryInfo block on 429 saying when the quota window
    reopens. Honouring it matters: free-tier limits are per *minute*, so plain exponential
    backoff from a small base gives up seconds before the quota would have refreshed.
    Falls back to exponential backoff when no hint is present.
    """
    for entry in _error_details(exc):
        if not isinstance(entry, dict) or "RetryInfo" not in str(entry.get("@type", "")):
            continue
        raw = str(entry.get("retryDelay", "")).strip().rstrip("s")
        try:
            return min(float(raw), _MAX_RETRY_SLEEP)
        except ValueError:
            break

    return min(RETRY_BASE_DELAY * (2**attempt), _MAX_RETRY_SLEEP)


class TranslationError(Exception):
    """Raised when translation fails. The message is user-facing."""


class _ModelUnavailable(Exception):
    """Internal: this model is retired or unknown, so try the next one in the chain."""


#: Models known to be out of daily quota or retired, remembered for this process only.
#: Without this, every chunk of a long document re-tries each dead model first — 20 wasted
#: round trips on a 10-chunk file. Cleared on restart, which is the right granularity: the
#: daily quota resets on its own cycle and a fresh process should check again.
_unavailable: set[str] = set()


def reset_model_availability() -> None:
    """Forget which models were unavailable. Exposed for tests and manual recovery."""
    _unavailable.clear()


class _GenerativeClient(Protocol):
    """The slice of genai.Client this module uses — lets tests pass a stub."""

    models: object


def split_into_chunks(text: str, max_chars: int = TRANSLATION_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most `max_chars`, preferring paragraph boundaries.

    Order is preserved and no content is dropped, so rejoining the translated chunks
    reconstructs the document. Paragraphs longer than `max_chars` fall back to sentence
    boundaries, then to a hard character split.

    Args:
        text: The text to split.
        max_chars: Maximum size of any returned chunk.

    Returns:
        Non-empty chunks in original order. An empty/whitespace input returns [].
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text.strip():
        return []

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        for piece in _fit(paragraph, max_chars):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece

    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _fit(paragraph: str, max_chars: int) -> list[str]:
    """Break one over-long paragraph into pieces that each fit in `max_chars`."""
    if len(paragraph) <= max_chars:
        return [paragraph]

    pieces: list[str] = []
    current = ""
    # Keep the delimiter attached to the sentence it ends.
    for sentence in paragraph.replace("। ", "।\n").replace(". ", ".\n").split("\n"):
        while len(sentence) > max_chars:
            # A single sentence longer than the cap — split it hard rather than fail.
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = sentence

    if current:
        pieces.append(current)
    return pieces


def build_client(api_key: str | None = None) -> genai.Client:
    """Create a Gemini client.

    Raises:
        TranslationError: if no API key is configured.
    """
    key = api_key or get_api_key()
    if not key:
        raise TranslationError(
            "No Gemini API key found. Add GEMINI_API_KEY to your .env file locally, or to "
            "the Secrets panel if you are running this on Streamlit Cloud."
        )
    try:
        return genai.Client(api_key=key)
    except Exception as exc:
        raise TranslationError(f"Could not initialise the Gemini client: {exc}") from exc


def translate(
    text: str,
    target_language: str,
    client: _GenerativeClient | None = None,
    progress_callback=None,
    status_callback=None,
) -> str:
    """Translate `text` into `target_language`, chunking long input.

    Args:
        text: Source text.
        target_language: Language name as Gemini should read it (from `languages.py`).
        client: Injected client; built from config when omitted. Tests pass a stub.
        progress_callback: Optional callable(done, total) invoked after each chunk.
        status_callback: Optional callable(message) invoked before a retry sleep, so a UI
            can explain a long pause instead of appearing frozen.

    Returns:
        The translated text, chunks rejoined in original order.

    Raises:
        TranslationError: empty input, over the length cap, or an API failure that
            survived the retry policy. The message is safe to show the user.
    """
    if not text or not text.strip():
        raise TranslationError("There is nothing to translate — enter some text first.")

    if len(text) > MAX_INPUT_CHARS:
        raise TranslationError(
            f"This text is {len(text):,} characters, over the {MAX_INPUT_CHARS:,} character "
            "limit. The limit exists because long documents are split into many API calls "
            "and the free tier is rate-limited. Please translate a shorter extract."
        )

    active = client or build_client()
    chunks = split_into_chunks(text)
    if not chunks:
        raise TranslationError("There is nothing to translate — enter some text first.")

    translated = []
    for index, chunk in enumerate(chunks, start=1):
        translated.append(
            _translate_chunk(active, chunk, target_language, status_callback, index, len(chunks))
        )
        if progress_callback:
            progress_callback(index, len(chunks))

    return "\n\n".join(translated)


def _translate_chunk(
    client: _GenerativeClient,
    chunk: str,
    target_language: str,
    status_callback=None,
    index: int = 1,
    total: int = 1,
) -> str:
    """Translate one chunk, falling through the model chain if a model has been retired."""
    chain = [m for m in (MODEL_NAME, *FALLBACK_MODELS) if m not in _unavailable]
    if not chain:
        # Everything was exhausted earlier in this run; re-check rather than hard-fail.
        reset_model_availability()
        chain = [MODEL_NAME, *FALLBACK_MODELS]

    last_error: Exception | None = None
    for model in chain:
        try:
            return _attempt(client, chunk, target_language, model, status_callback, index, total)
        except _ModelUnavailable as exc:
            _unavailable.add(model)
            last_error = exc
            continue

    if "daily quota" in str(last_error):
        raise TranslationError(
            "Every available model has used up its free-tier requests for today. The free "
            "tier allows a limited number of requests per model per day, and this counts "
            "each part of a long document separately. The quota resets on a daily cycle — "
            "try again later, translate shorter extracts, or enable billing in Google AI "
            "Studio for higher limits."
        )
    raise TranslationError(
        "None of the configured Gemini models are available to this API key — they have "
        f"most likely been retired. Update MODEL_NAME in src/config.py. ({last_error})"
    )


def _attempt(
    client: _GenerativeClient,
    chunk: str,
    target_language: str,
    model: str,
    status_callback=None,
    index: int = 1,
    total: int = 1,
) -> str:
    """Call one model, retrying with exponential backoff on transient failures."""
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION.format(target=target_language),
        temperature=0.2,
    )

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model, contents=chunk, config=config
            )
            result = (response.text or "").strip()
            if not result:
                raise TranslationError(
                    "Gemini returned an empty translation. This usually means the content "
                    "was blocked by a safety filter. Try rephrasing or a different extract."
                )
            return result

        except errors.APIError as exc:
            last_error = exc
            code = getattr(exc, "code", None)
            if code == 404:
                # Retired or unknown model — no amount of retrying helps; try the next one.
                raise _ModelUnavailable(f"{model}: {exc}") from exc
            if code == 429 and _daily_quota_exhausted(exc):
                # This model is out of free-tier requests for the day. The cap is per
                # model, so the next one in the chain may still have budget. Waiting
                # would not help; the API's retryDelay hint is misleading here.
                if status_callback:
                    status_callback(
                        f"Daily free-tier limit reached for {model} — switching to a "
                        "backup model…"
                    )
                raise _ModelUnavailable(f"{model} daily quota exhausted: {exc}") from exc
            if code in _RETRYABLE_CODES and attempt < MAX_RETRIES - 1:
                delay = _retry_delay(exc, attempt)
                if status_callback:
                    reason = "Rate limit reached" if code == 429 else "Gemini is busy"
                    status_callback(
                        f"{reason} — waiting {delay:.0f}s before retrying "
                        f"part {index} of {total}…"
                    )
                time.sleep(delay)
                continue
            raise TranslationError(_message_for(exc, code)) from exc

        except TranslationError:
            raise

        except Exception as exc:  # network stack, DNS, timeouts
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY * (2**attempt))
                continue
            raise TranslationError(
                "Could not reach the Gemini API. Check your internet connection and try "
                f"again. ({exc})"
            ) from exc

    raise TranslationError(f"Translation failed after {MAX_RETRIES} attempts. ({last_error})")


def _message_for(exc: Exception, code: int | None) -> str:
    """Map an SDK error onto something a user can act on."""
    if code == 429:
        if _daily_quota_exhausted(exc):
            return (
                "The free-tier daily limit for this model has been reached. Unlike a "
                "per-minute limit, waiting a few seconds will not help — the quota resets "
                "on a daily cycle. Try a shorter extract later, or enable billing in "
                "Google AI Studio for higher limits."
            )
        return (
            "Gemini's per-minute rate limit was hit. Wait a minute and try again, or "
            "translate a shorter piece of text."
        )
    if code in (401, 403):
        return (
            "Gemini rejected the API key. Check that GEMINI_API_KEY is correct and that the "
            "Generative Language API is enabled for it in Google AI Studio."
        )
    if code == 400:
        return f"Gemini rejected the request as invalid. ({exc})"
    if code in (500, 502, 503, 504):
        return "Gemini is temporarily unavailable. Please try again in a moment."
    return f"Translation failed: {exc}"

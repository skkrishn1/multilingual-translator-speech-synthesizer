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


def _retry_delay(exc: Exception, attempt: int) -> float:
    """How long to wait before the next attempt.

    Gemini returns a google.rpc.RetryInfo block on 429 saying when the quota window
    reopens. Honouring it matters: free-tier limits are per *minute*, so plain exponential
    backoff from a small base gives up seconds before the quota would have refreshed.
    Falls back to exponential backoff when no hint is present.
    """
    details = getattr(exc, "details", None)
    entries = []
    if isinstance(details, dict):
        error = details.get("error", details)
        if isinstance(error, dict):
            entries = error.get("details", []) or []

    for entry in entries:
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
) -> str:
    """Translate `text` into `target_language`, chunking long input.

    Args:
        text: Source text.
        target_language: Language name as Gemini should read it (from `languages.py`).
        client: Injected client; built from config when omitted. Tests pass a stub.
        progress_callback: Optional callable(done, total) invoked after each chunk.

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
        translated.append(_translate_chunk(active, chunk, target_language))
        if progress_callback:
            progress_callback(index, len(chunks))

    return "\n\n".join(translated)


def _translate_chunk(client: _GenerativeClient, chunk: str, target_language: str) -> str:
    """Translate one chunk, falling through the model chain if a model has been retired."""
    last_error: Exception | None = None
    for model in (MODEL_NAME, *FALLBACK_MODELS):
        try:
            return _attempt(client, chunk, target_language, model)
        except _ModelUnavailable as exc:
            last_error = exc
            continue

    raise TranslationError(
        "None of the configured Gemini models are available to this API key — they have "
        f"most likely been retired. Update MODEL_NAME in src/config.py. ({last_error})"
    )


def _attempt(
    client: _GenerativeClient, chunk: str, target_language: str, model: str
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
            if code in _RETRYABLE_CODES and attempt < MAX_RETRIES - 1:
                time.sleep(_retry_delay(exc, attempt))
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
        return (
            "Gemini's rate limit was hit. The free tier allows a limited number of requests "
            "per minute and per day. Wait a minute and try again, or translate a shorter "
            "piece of text."
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

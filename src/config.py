"""Configuration: API key resolution and the size/length limits used across the app.

Key resolution order is st.secrets (Streamlit Cloud) then .env (local). Importing this
module never raises when the key is absent — callers check `get_api_key()` and show a
message, because a missing key is a normal first-run state, not a crash.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

#: Gemini model used for translation. A Flash model balances quality against free-tier limits.
MODEL_NAME = "gemini-3.6-flash"

#: Tried in order if MODEL_NAME returns 404. Google retires models on its own schedule —
#: gemini-2.5-flash became unavailable to new keys during this project's development — so a
#: deployment left running for months should degrade to a working model rather than break.
FALLBACK_MODELS = ("gemini-flash-latest", "gemini-3.5-flash-lite")

#: Upload cap. Mirrors maxUploadSize in .streamlit/config.toml.
MAX_FILE_MB = 10
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

#: Hard cap on characters sent to Gemini in one run. A long document becomes many chunks,
#: and the free tier is rate-limited per minute — this stops one upload eating the day's quota.
MAX_INPUT_CHARS = 20_000

#: Paragraph-aware chunk size for translation requests.
TRANSLATION_CHUNK_CHARS = 3_000

#: gTTS rejects very long strings; this is the per-request cap before concatenation.
TTS_CHUNK_CHARS = 1_500

#: Retry policy for rate-limited / transient Gemini failures. The base delay is the
#: fallback when the API sends no RetryInfo; free-tier limits are per-minute, so a small
#: base would give up before the quota window reopens.
MAX_RETRIES = 3
RETRY_BASE_DELAY = 8.0

_ENV_VAR = "GEMINI_API_KEY"


def get_api_key() -> str | None:
    """Return the Gemini API key, or None if it is not configured anywhere.

    Checks st.secrets first so a Streamlit Cloud deployment needs no .env file. The
    import is local and guarded: st.secrets raises when no secrets.toml exists, and this
    module must stay importable from unit tests that never start Streamlit.
    """
    try:
        import streamlit as st

        value = st.secrets.get(_ENV_VAR)
        if value:
            return str(value).strip()
    except Exception:
        pass

    value = os.environ.get(_ENV_VAR)
    return value.strip() if value else None


def has_api_key() -> bool:
    return bool(get_api_key())

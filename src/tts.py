"""Text-to-speech via gTTS, returning MP3 bytes held in memory.

Nothing is written to disk: Streamlit Cloud's filesystem is ephemeral, and bytes feed
st.audio and st.download_button directly.

gTTS is an unofficial client for Google Translate's speech endpoint. It has no API key and
is rate-limited by IP, so failures here are expected occasionally and are mapped to a
retry message rather than a traceback.
"""

from __future__ import annotations

import io

from gtts import gTTS
from gtts.tts import gTTSError

from .config import TTS_CHUNK_CHARS


class TTSError(Exception):
    """Raised when speech synthesis fails. The message is user-facing."""


def split_for_speech(text: str, max_chars: int = TTS_CHUNK_CHARS) -> list[str]:
    """Split text into pieces gTTS will accept, preferring line boundaries.

    Order is preserved — the caller concatenates the resulting MP3s in sequence.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text.strip():
        return []

    chunks: list[str] = []
    current = ""

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        while len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = line

    if current:
        chunks.append(current)
    return chunks


def synthesize(text: str, tts_code: str | None, tld: str = "com", slow: bool = False) -> bytes:
    """Convert text to MP3 bytes.

    Long text is split and each piece synthesized separately; MP3 frames concatenate
    cleanly, so the pieces are written into one buffer and play as a single track.

    Args:
        text: Text to speak — normally the translated output.
        tts_code: gTTS language code. None means the language has no voice.
        tld: Google Translate host suffix, controlling accent.
        slow: Speak slowly.

    Returns:
        MP3 file contents.

    Raises:
        TTSError: no voice for this language, empty text, or a gTTS/network failure.
    """
    if tts_code is None:
        raise TTSError(
            "This language is not available in the text-to-speech engine. The translation "
            "above is still correct — only audio is unavailable."
        )
    if not text or not text.strip():
        raise TTSError("There is no text to convert to speech.")

    pieces = split_for_speech(text)
    if not pieces:
        raise TTSError("There is no text to convert to speech.")

    buffer = io.BytesIO()
    for piece in pieces:
        try:
            gTTS(text=piece, lang=tts_code, tld=tld, slow=slow).write_to_fp(buffer)
        except gTTSError as exc:
            raise TTSError(_message_for(exc)) from exc
        except ValueError as exc:
            # gTTS raises ValueError for an unknown language code.
            raise TTSError(f"'{tts_code}' is not a language this speech engine supports. ({exc})") from exc
        except Exception as exc:
            raise TTSError(
                f"Could not reach the speech service. Check your connection and try again. ({exc})"
            ) from exc

    audio = buffer.getvalue()
    if not audio:
        raise TTSError("The speech service returned no audio. Please try again.")
    return audio


def _message_for(exc: gTTSError) -> str:
    detail = str(exc)
    if "429" in detail or "Too Many Requests" in detail:
        return (
            "The free speech service is rate-limiting this address. Wait a minute and try "
            "again — this is a limit of the unofficial gTTS endpoint, not of your input."
        )
    if "200" not in detail and ("Failed to connect" in detail or "connection" in detail.lower()):
        return "Could not reach the speech service. Check your internet connection and try again."
    return f"Speech synthesis failed: {detail}"

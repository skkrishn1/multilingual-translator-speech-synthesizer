"""Streamlit entry point — user interface only.

Streamlit re-runs this file top to bottom on every widget interaction, so it holds no
business logic: every external call lives behind a function in src/. Results are computed
once on a button press and kept in st.session_state, because a download click re-runs the
script and would otherwise re-call Gemini and gTTS on every click.
"""

from __future__ import annotations

import hashlib
import html

import streamlit as st

from src import languages
from src.config import MAX_FILE_MB, MAX_INPUT_CHARS, has_api_key
from src.extractors import SUPPORTED_EXTENSIONS, ExtractionError, extract_text
from src.translator import TranslationError, split_into_chunks, translate
from src.tts import TTSError, synthesize

st.set_page_config(
    page_title="Multilingual Translator & Speech Synthesizer",
    page_icon="🌐",
    layout="wide",
)

st.markdown(
    """
    <style>
      /* Translucent rather than a fixed colour, so the box still reads correctly if the
         theme in .streamlit/config.toml is switched back to light. */
      .result-box {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 10px;
        padding: 1rem 1.25rem;
        line-height: 1.8;
        font-size: 1.02rem;
        white-space: pre-wrap;
        max-height: 26rem;
        overflow-y: auto;
      }
      /* Non-Latin scripts (Tamil, Hindi, Arabic) render small at the default size. */
      .result-box[dir="rtl"] {
        font-size: 1.15rem;
        line-height: 2;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Session state ---------------------------------------------------------------------

for key, default in {
    "translated_text": "",
    "translated_into": "",
    "source_used": "",
    "audio_bytes": None,
    "audio_for": "",
}.items():
    st.session_state.setdefault(key, default)


def _reset_results() -> None:
    st.session_state.update(
        translated_text="", translated_into="", source_used="", audio_bytes=None, audio_for=""
    )


# --- Cached wrappers -------------------------------------------------------------------
# Arguments starting with an underscore are excluded from the cache key, so these are keyed
# on the content hash and target only — identical input is never paid for twice.


@st.cache_data(show_spinner=False)
def cached_translate(
    _text: str, text_hash: str, gemini_name: str, _progress=None, _status=None
) -> str:
    return translate(_text, gemini_name, progress_callback=_progress, status_callback=_status)


@st.cache_data(show_spinner=False)
def cached_synthesize(_text: str, text_hash: str, tts_code: str, tld: str) -> bytes:
    return synthesize(_text, tts_code, tld)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Sidebar ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")

    target_display = st.selectbox(
        "Translate into",
        languages.supported_languages(),
        index=languages.supported_languages().index("Hindi"),
        help="Every language here can be translated. A few have no voice available for audio.",
    )
    target = languages.get_language(target_display)

    if target.speakable:
        st.caption(f"Audio available · voice code `{target.tts_code}`")
    else:
        st.caption("Translation only — no voice available for this language.")

    st.divider()
    st.subheader("How to use")
    st.markdown(
        f"""
1. Pick a target language above.
2. Paste text, or upload a `.pdf`, `.txt`, `.csv`, or `.xlsx` file (max {MAX_FILE_MB} MB).
3. Press **Translate**.
4. Press **Generate audio**, then download the MP3.

Input is capped at {MAX_INPUT_CHARS:,} characters per translation.
        """
    )

    st.divider()
    if has_api_key():
        st.success("Gemini API key detected", icon="✅")
    else:
        st.error(
            "No Gemini API key found. Add `GEMINI_API_KEY` to a `.env` file locally, or to "
            "the Secrets panel on Streamlit Cloud.",
            icon="🔑",
        )

    st.warning(
        "Do not paste confidential material. On Gemini's free tier, inputs may be used to "
        "improve Google's models.",
        icon="⚠️",
    )

# --- Input -----------------------------------------------------------------------------

st.title("🌐 Multilingual Text Translator & Speech Synthesizer")
st.caption("Translate text or documents with Google Gemini, then hear and download the result.")

source_text = ""
typed_tab, upload_tab = st.tabs(["✍️ Type or paste", "📄 Upload a file"])

with typed_tab:
    source_text = st.text_area(
        "Text to translate",
        height=220,
        placeholder="Type or paste your text here…",
        label_visibility="collapsed",
    )

with upload_tab:
    uploaded = st.file_uploader(
        "Choose a file",
        type=list(SUPPORTED_EXTENSIONS),
        help=f"PDF, TXT, CSV or XLSX, up to {MAX_FILE_MB} MB. Scanned PDFs cannot be read.",
    )
    if uploaded is not None:
        try:
            extracted = extract_text(uploaded.getvalue(), uploaded.name)
        except ExtractionError as exc:
            st.error(str(exc), icon="🚫")
        except Exception as exc:  # last resort — a traceback must never reach the user
            st.error(f"That file could not be read: {exc}", icon="🚫")
        else:
            source_text = extracted
            st.success(f"Extracted {len(extracted):,} characters from **{uploaded.name}**")
            with st.expander("Preview extracted text", expanded=False):
                st.text(extracted[:3000] + ("\n\n… (truncated preview)" if len(extracted) > 3000 else ""))

# --- Translate -------------------------------------------------------------------------

if source_text:
    over_limit = len(source_text) > MAX_INPUT_CHARS
    parts = len(split_into_chunks(source_text))
    detail = f"{len(source_text):,} characters"
    if over_limit:
        detail += f" — over the {MAX_INPUT_CHARS:,} limit"
    elif parts > 1:
        # Set expectations before the click: multi-part runs take noticeably longer.
        detail += f" — will be translated in {parts} parts, so this will take a little longer"
    st.caption(detail)

translate_clicked = st.button(
    f"Translate into {target_display}",
    type="primary",
    disabled=not source_text.strip() or not has_api_key(),
    use_container_width=False,
)

if translate_clicked:
    _reset_results()
    total_chunks = max(1, len(split_into_chunks(source_text)))
    opening = (
        "Translating…" if total_chunks == 1
        else f"Translating part 1 of {total_chunks}… (long text is split into parts)"
    )
    progress = st.progress(0.0, text=opening)
    fraction = [0.0]  # mutable so both callbacks share the latest position

    def on_progress(done: int, total: int) -> None:
        fraction[0] = done / total
        nxt = f"Translating part {done + 1} of {total}…" if done < total else "Finishing…"
        progress.progress(fraction[0], text=nxt)

    def on_status(message: str) -> None:
        # A rate-limit wait can be 45s of silence; say so rather than look frozen.
        progress.progress(fraction[0], text=message)

    try:
        result = cached_translate(
            source_text, _digest(source_text), target.gemini_name, on_progress, on_status
        )
    except TranslationError as exc:
        progress.empty()
        st.error(str(exc), icon="🚫")
    except Exception as exc:
        progress.empty()
        st.error(f"Unexpected problem during translation: {exc}", icon="🚫")
    else:
        progress.empty()
        st.session_state.update(
            translated_text=result, translated_into=target_display, source_used=source_text
        )

# --- Results ---------------------------------------------------------------------------

if st.session_state.translated_text:
    translated = st.session_state.translated_text
    into = st.session_state.translated_into

    st.divider()
    st.subheader(f"Translation — {into}")

    if into != target_display:
        st.info(
            f"This result is in {into}. Press Translate again to redo it in {target_display}.",
            icon="ℹ️",
        )

    direction = "rtl" if languages.is_rtl(into) else "ltr"
    st.markdown(
        f'<div class="result-box" dir="{direction}">{html.escape(translated)}</div>',
        unsafe_allow_html=True,
    )

    st.download_button(
        "⬇️ Download translation (.txt)",
        data=translated.encode("utf-8"),
        file_name=f"translation_{into.replace(' ', '_').lower()}.txt",
        mime="text/plain",
    )

    # --- Audio -------------------------------------------------------------------------

    st.divider()
    st.subheader("Audio")

    result_lang = languages.get_language(into)
    if not result_lang.speakable:
        st.info(
            f"{into} has no voice in the speech engine, so audio cannot be generated. "
            "The translation above is unaffected.",
            icon="🔇",
        )
    else:
        if st.button("🔊 Generate audio", use_container_width=False):
            with st.spinner("Generating speech…"):
                try:
                    audio = cached_synthesize(
                        translated, _digest(translated), result_lang.tts_code, result_lang.tld
                    )
                except TTSError as exc:
                    st.error(str(exc), icon="🔇")
                except Exception as exc:
                    st.error(f"Unexpected problem generating audio: {exc}", icon="🔇")
                else:
                    st.session_state.update(audio_bytes=audio, audio_for=into)

        if st.session_state.audio_bytes and st.session_state.audio_for == into:
            st.audio(st.session_state.audio_bytes, format="audio/mp3")
            st.download_button(
                "⬇️ Download audio (.mp3)",
                data=st.session_state.audio_bytes,
                file_name=f"speech_{into.replace(' ', '_').lower()}.mp3",
                mime="audio/mpeg",
            )

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 0–4 and 6 of `PLAN.md` are done: all modules, the UI, 57 passing tests, sample
fixtures, and the README. Outstanding: live end-to-end verification with a real API key,
and Phase 5 deployment. `PLAN.md` remains the design spec — update it if a decision changes.

This is a graded PGP capstone. The brief requires a documentation deliverable covering
setup, the Gemini API key, **limitations**, and **challenges faced** — the last two are
graded sections, not filler, and live in `README.md`.

## Environment

- `python` is **not** on PATH — it resolves to the Microsoft Store shim and fails. Use
  `py -3.14`, or `.\.venv\Scripts\python.exe` directly.
- Local interpreter is Python 3.14.2, but **Streamlit Community Cloud caps at 3.13**.
  `runtime.txt` pins 3.13 — avoid 3.14-only syntax.
- The venv is at `.venv`; only Python 3.14 is installed locally.
- `.env` holds the key locally and is gitignored, as is `.streamlit/secrets.toml`. Never
  stage either. Nothing has been committed or pushed yet — the tree is staged only.

## Commands

```powershell
.\.venv\Scripts\python.exe -m pytest                          # full suite, no network
.\.venv\Scripts\python.exe -m pytest tests/test_tts.py -v     # one file
.\.venv\Scripts\python.exe -m pytest tests/test_translator.py::TestChunking -v
.\.venv\Scripts\python.exe scripts\check_api_key.py           # one live call, diagnoses 403s
.\.venv\Scripts\streamlit.exe run app.py                      # local app on :8501
```

PowerShell here-strings break when passed to `python -c`; write a script to the scratchpad
and run that instead.

## Architecture

`app.py` holds **UI only, zero business logic**. Everything that touches the network, the
filesystem, or a parser lives in `src/` as a plain function:

- `src/config.py` — API key resolution (`st.secrets` → `.env`), size/length caps
- `src/languages.py` — the language registry
- `src/extractors.py` — PDF/TXT/CSV/XLSX → plain text, dispatched on extension
- `src/translator.py` — Gemini wrapper: prompt, chunking, retry/backoff, error mapping
- `src/tts.py` — text → in-memory MP3 bytes

The reason is mechanical: Streamlit re-executes the entire script top-to-bottom on every
widget interaction. Logic placed in `app.py` runs on every click; logic in `src/` is called
deliberately and is unit-testable without an API key or network.

### The four constraints that break this project if ignored

**One language registry.** Gemini translates into hundreds of languages; gTTS speaks 69,
under its own short codes (`hi`, `ta`, `fr`). The sets differ. A single dict in
`languages.py` maps display name → (Gemini target name, optional gTTS code, tld). A language
with no gTTS code must still translate — the audio section shows an explanation instead of
failing. Never derive this mapping inline in the UI. `test_languages.py` validates every
code against `gtts.lang.tts_langs()`; keep that test, since a wrong code fails only at
synthesis time.

**Results live in `st.session_state`.** Clicking `st.download_button` re-runs the script.
Without session state, downloading the MP3 re-calls Gemini and gTTS on every click, burning
free-tier quota. Compute once on button press, store, and let the rest of the script read.

**Cache keyed on content.** `@st.cache_data` wraps translation and synthesis. The text is
passed as an underscore-prefixed arg so Streamlit excludes it from the key, leaving
`(sha256(text), target)` as the actual cache key.

**Chunk with order preservation.** Split on paragraph boundaries into ~3000-char chunks,
translate each, rejoin in order. `tts.py` does the same and concatenates MP3 frames.

## Testing

The suite must keep passing with **no API key and no network**. Gemini is a stub client in
`tests/test_translator.py` replaying scripted responses/errors; gTTS is monkeypatched to
write a byte marker. PDF fixtures are byte-level PDFs built in `tests/conftest.py` (with
hand-computed xref offsets) so no PDF-authoring dependency is needed — `make_blank_pdf()`
is the scanned-PDF case. An autouse fixture stubs `time.sleep` so retry backoff is instant.

When writing chunking tests, make the sample text genuinely exceed the real cap
(`TRANSLATION_CHUNK_CHARS` 3000, `TTS_CHUNK_CHARS` 1500) — short fixtures silently produce
one chunk and assert nothing.

## Known constraints to design around

- **Gemini free tier** rate-limits per minute and per day; a long document split into many
  chunks fires them in quick succession and *will* 429 (reproduced on a 4.8k-char file).
  Retries honour the API's `RetryInfo` delay rather than guessing — don't replace that with
  plain exponential backoff, which gives up before a per-minute window reopens.
- **Models get retired.** `gemini-2.5-flash` 404'd for new keys mid-project. `MODEL_NAME` +
  `FALLBACK_MODELS` in `config.py` form a chain the translator advances through on 404.
  `thinking_budget=0` is an *invalid argument* on Gemini 3 models — don't reintroduce it.
- **Free-tier inputs train Google's models.** The app must carry a notice not to upload
  confidential material, and the README must state it.
- **gTTS is an unofficial client** with no API key, rate-limited by IP. Catch `gTTSError`
  explicitly and show a retry message.
- **Scanned PDFs yield empty text** from pypdf. Detect and warn; OCR is out of scope.
- Every failure path (no API key, empty input, oversized file, quota exceeded, timeout,
  language without TTS) must show a message, never a traceback.

## Deployment

Streamlit Community Cloud, API key via the Secrets UI. The brief names Heroku, but its free
tier ended in November 2022 — document the substitution rather than silently deviating.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 0–4 and 6 of `PLAN.md` are done: all modules, the UI, 72 passing tests, sample
fixtures, and the README. Phase 5 is deployed and public at
<https://multilingual-translator-speech-synthesizer.streamlit.app/> — Python 3.14, key in
the Cloud Secrets panel, sharing set to "public and searchable" and confirmed by a third
party in their own browser. Outstanding: the live end-to-end pass across all four file
formats and a language without a gTTS voice.

Do not use `curl` to test whether the deployed app is publicly reachable. Streamlit's auth
endpoint issues an anonymous session cookie and bounces browsers back to the app, so a
cookie-less client sees a `303` to `/-/login` and looks locked out when it is not. Test in
a private browsing window, or ask someone else to open the link. `PLAN.md` remains the design spec — update it if a decision changes.

This is a graded PGP capstone. The brief requires a documentation deliverable covering
setup, the Gemini API key, **limitations**, and **challenges faced** — the last two are
graded sections, not filler, and live in `README.md`.

## Environment

- `python` is **not** on PATH — it resolves to the Microsoft Store shim and fails. Use
  `py -3.14`, or `.\.venv\Scripts\python.exe` directly.
- Local interpreter is Python 3.14.2 and the deployment targets **3.14** to match, so the
  old "avoid 3.14-only syntax" rule no longer applies. Cloud supports 3.10–3.14.
  **`runtime.txt` does not set the version** — Streamlit Cloud ignores it; the version comes
  from the Advanced settings dropdown at deploy time and cannot be changed without deleting
  and redeploying the app. The file is kept only as a record of the tested interpreter.
- The venv is at `.venv`; only Python 3.14 is installed locally.
- `.env` holds the key locally and is gitignored, as is `.streamlit/secrets.toml`. Never
  stage either. The work is committed and pushed to the public repo
  `skkrishn1/multilingual-translator-speech-synthesizer` (`origin/main`); history has been
  checked and contains no key.

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

**Long runs must report progress.** `translate()` takes `progress_callback(done, total)`
and `status_callback(message)`; `app.py` passes both through `cached_translate` as
underscore-prefixed args so they stay out of the cache key. A shipped bug created the
progress bar but never updated it, so multi-chunk documents looked hung — accepting a
callback is not the same as wiring it up.

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

- **Gemini free tier: the binding limit is per DAY, per MODEL** — measured at 20/day for
  `gemini-3.6-flash`. Each chunk is one request, so one long upload burns a lot of it.
  A 429 must be classified before acting: `_daily_quota_exhausted()` reads the
  `QuotaFailure` violation's `quotaId` for `PerDay`. Daily → switch model immediately (the
  quota is per model, so sleeping is useless and the API's `retryDelay` hint misleads).
  Per-minute → sleep the `RetryInfo` delay and retry the same model. Don't collapse these
  two paths back together.
- `_unavailable` in `translator.py` memoises dead models per process. Tests reset it via
  the autouse `fresh_model_availability` fixture — module state leaks between tests.
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

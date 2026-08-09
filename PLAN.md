# Capstone Project Plan — Multilingual Text Translator & Speech Synthesizer

**Stack:** Streamlit · Google Gemini API (translation) · gTTS (speech) · Python 3.14
**Deliverables:** working deployed app + documentation (setup, usage, limitations, challenges)

---

## 1. Architecture

### Design principle

The Streamlit script holds **no business logic**. Every external call — Gemini, gTTS, file
parsing — sits behind a plain Python module in `src/`. This matters for three reasons:
Streamlit re-runs the whole script top-to-bottom on every widget interaction (so logic in
`app.py` gets re-executed constantly), the logic stays unit-testable without an API key or
network, and the grader can read the translation logic without reading UI code.

### Component layout

```
User (browser)
      │
      ▼
┌─────────────────────────────────────────────┐
│ app.py — Streamlit UI                       │
│  input tabs · language dropdown · buttons   │
│  st.session_state (translated text + audio) │
└─────────────────────────────────────────────┘
      │            │              │
      ▼            ▼              ▼
┌───────────┐ ┌──────────┐ ┌──────────┐
│extractors │ │translator│ │   tts    │
│  .py      │ │   .py    │ │   .py    │
└───────────┘ └──────────┘ └──────────┘
  pypdf         Gemini API     gTTS
  pandas        (network)      (network)
  openpyxl          │              │
                    ▼              ▼
              ┌──────────────────────────┐
              │ languages.py             │
              │ single source of truth:  │
              │ display name → gemini    │
              │ name + gTTS code + tld   │
              └──────────────────────────┘
```

### File structure

```
Capstone_Project/
├── app.py                     # Streamlit entry point — UI only
├── src/
│   ├── __init__.py
│   ├── config.py              # API key resolution, size limits, constants
│   ├── languages.py           # language registry (the critical mapping)
│   ├── extractors.py          # PDF / TXT / CSV / XLSX → plain text
│   ├── translator.py          # Gemini wrapper: prompt, chunking, retries
│   └── tts.py                 # gTTS wrapper: text → MP3 bytes
├── tests/
│   ├── test_extractors.py
│   ├── test_languages.py
│   └── test_translator.py     # mocked Gemini client — no network
├── sample_files/              # fixtures for the manual test matrix
├── .streamlit/config.toml     # theme + upload size cap
├── requirements.txt
├── runtime.txt                # records the tested Python version (Cloud ignores it)
├── .env.example
├── .gitignore                 # must exclude .env and secrets.toml
└── README.md                  # the documentation deliverable
```

### Four decisions worth stating up front

**Language registry as single source of truth.** Gemini can translate into hundreds of
languages; gTTS speaks about 60, using its own short codes (`hi`, `ta`, `fr`). These sets are
not the same. One dict in `languages.py` maps a display name to both a Gemini target name and
an optional gTTS code. When a language has no gTTS code, translation still works and the audio
section shows an explanatory message instead of failing. Building this mapping ad-hoc in the
UI is the single most common way this project breaks.

**Session state for results.** `st.download_button` triggers a script re-run when clicked.
Without holding the translated text and audio bytes in `st.session_state`, downloading the MP3
re-calls Gemini and gTTS — slower, and it burns API quota on every click. Results are computed
once on button press and stored; the rest of the script only reads them.

**Caching keyed on content.** `@st.cache_data` wraps the translation call keyed on
`(sha256(text), target_language)`, so re-translating identical input is free.

**Chunking with order preservation.** Long documents exceed practical prompt sizes, so
`translator.py` splits on paragraph boundaries into ~3000-character chunks, translates each,
and rejoins in order. `tts.py` does the same for audio and concatenates the MP3 frames.

---

## 2. Task breakdown

### Phase 0 — Setup (blocking, do first)

- Create virtual environment, install dependencies, freeze `requirements.txt`
- **Obtain a Gemini API key** from Google AI Studio — this blocks all translation work
- Initialize git repo, write `.gitignore` (secrets excluded before the first commit)
- Scaffold the folder structure above

### Phase 1 — Core modules (no UI yet)

- `config.py` — resolve API key from `st.secrets` then `.env`, define size/length limits
- `languages.py` — the registry, plus helpers `supported_languages()` and `tts_code_for()`
- `extractors.py` — one function per format, dispatched on file extension:
  PDF via pypdf, TXT via decode with encoding fallback, CSV/XLSX via pandas
  Includes empty-extraction detection (scanned PDFs yield no text — warn, don't crash)
- `translator.py` — client init, prompt construction, chunking, retry with exponential
  backoff on rate limits, and mapping SDK exceptions to plain user-facing messages
- `tts.py` — text to in-memory MP3 bytes, chunk-and-concatenate for long input

### Phase 2 — User interface

- Page config, title, sidebar with instructions and language dropdown
- Two input modes: direct text area and file uploader (`pdf`, `txt`, `csv`, `xlsx`)
- Extracted-text preview with character count
- Translate button → spinner → result panel
- Generate-audio button → `st.audio` player → `st.download_button` for the MP3
- Wire everything through `st.session_state`

### Phase 3 — Robustness and UX

- Guard every failure path: no API key, empty input, oversized file, unreadable file,
  API quota exceeded, network timeout, language without TTS support
- Input size caps with a clear message rather than a silent truncation
- Inline instructions so the app is self-explanatory

### Phase 4 — Testing

- Unit tests for extractors (all four formats), the language registry, and the chunker
- Mocked-client tests for `translator.py` — must pass with no API key present
- Manual matrix: 4 file types × 5 target languages (including a right-to-left language
  such as Arabic and a non-Latin script such as Tamil or Hindi), plus one long document
  and one language that has no gTTS voice

### Phase 5 — Deployment

- [x] Push to a public GitHub repository
- [x] Deploy on **Streamlit Community Cloud** (free); add the API key via the Secrets UI —
  live at <https://multilingual-translator-speech-synthesizer.streamlit.app/>
- [x] Select the Python version in the **Advanced settings** dialog when creating the app —
  `runtime.txt` is ignored by Community Cloud; see the risk note below. Deployed on 3.14.
- [x] Set viewer access to **public** so reviewers need no Streamlit account — verified by
  a third party opening the link in their own browser
- [ ] Verify the deployed app end-to-end across all four file formats, a language with a
  gTTS voice and one without

### Phase 6 — Documentation

- `README.md`: overview, architecture summary, setup steps, how to obtain and configure the
  Gemini API key, usage guide, and the two sections the brief explicitly asks for —
  **limitations** and **challenges faced**

---

## 3. Estimation

Two different questions get two different answers. The solo column is what this costs a
developer writing every line themselves — useful context for the write-up. The other column is
elapsed time when I write the code and you review, test, and deploy.

| Phase | With me writing | Solo |
|---|---|---|
| 0 — Setup and API key | 15 min (yours) | 2 h |
| 1 — Core modules | 30 min | 12 h |
| 2 — Streamlit UI | 20 min | 6 h |
| 3 — Robustness and UX | 15 min | 4 h |
| 4 — Testing (unit + manual matrix) | 45 min | 5 h |
| 5 — Deployment | 30 min (mostly yours) | 3 h |
| 6 — Documentation | 15 min | 4 h |
| **Total** | **~2.5 h** | **~36 h** |

Of that 2.5 hours, maybe an hour is genuinely your attention — obtaining the API key,
clicking through the test matrix to confirm translations read correctly, and authorizing the
GitHub push and Streamlit Cloud deploy. The rest is me writing and you skimming.

Realistically this is **one working session** to get it running locally and tested, plus a
short second one for deployment and the write-up. The split exists because deployment depends
on your accounts, not because the work is large.

The critical path runs through the Gemini API key. Everything downstream of translation is
blocked until it exists, so obtain it before anything else. Phases 1 and 2 have some
parallelism — the UI can be built against stub functions while the translator is finished.

---

## 4. Risks

**~~Streamlit Cloud does not run Python 3.14.~~ Resolved — the risk was misdiagnosed.**
This was planned around on the belief that Community Cloud capped at 3.13 and that
`runtime.txt` would pin it. Both turned out to be wrong: Cloud supports 3.10–3.14, and it
**ignores `runtime.txt` entirely** — the version comes from a dropdown in the Advanced
settings dialog at app-creation time and cannot be changed afterwards without deleting and
redeploying. The deployment therefore targets **3.14**, matching the local 3.14.2, and
`runtime.txt` is kept only as a record of the tested interpreter. The real deployment-day
surprise was not the version but the mechanism.

**Heroku has no free tier.** The brief names Heroku as a deployment option, but free dynos
were discontinued in November 2022. Streamlit Community Cloud is free and purpose-built for
this stack — worth noting the substitution in the documentation.

**gTTS is an unofficial client** for Google Translate's speech endpoint. It has no API key,
is rate-limited by IP, and can break without notice. On shared cloud hosting this occasionally
produces HTTP 429s. Mitigation: catch `gTTSError` explicitly, show a retry message, and
document the constraint honestly.

**Gemini free-tier rate limits** are per-minute and per-day, and they bite exactly where this
project is weakest: a long document split into many chunks fires many requests in quick
succession. Reported free-tier limits are roughly 10 requests/minute and 250/day for Gemini
2.5 Flash, and 15/minute with 1,000/day for Flash-Lite (confirm current values in AI Studio —
Google revises these). Default to **Gemini 2.5 Flash** for translation quality, keep the
retry-with-backoff in `translator.py`, and cap document length so a single upload cannot
consume the daily quota.

**Free-tier inputs are used to improve Google's models.** Paid tier excludes this; the free
tier does not. That is a genuine constraint for a translation tool, since users may paste
sensitive documents. It belongs in the README's limitations section, and the app should carry
a short notice telling users not to upload confidential material.

**Scanned PDFs contain no extractable text.** pypdf returns empty strings for image-only
pages. Detect this and tell the user rather than translating nothing; OCR is out of scope.

**Translation quality is unverifiable without a speaker** of the target language. Spot-check
with round-trip translation and note the limitation in the documentation.

---

## 5. Definition of done

- App runs locally and deployed, with a public URL
- All four file formats extract correctly; five-plus target languages verified
- MP3 downloads and plays
- Every listed failure path shows a message instead of a traceback
- Unit tests pass without an API key present
- No secret committed to git
- README covers setup, usage, limitations, and challenges

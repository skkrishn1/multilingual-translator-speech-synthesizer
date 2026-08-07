# Multilingual Text Translator & Speech Synthesizer

A Streamlit web app that translates text or uploaded documents into any of 50+ languages
using Google's Gemini API, then speaks the translation aloud and offers it as a
downloadable MP3.

Built for the Generative AI and ML capstone project.

---

## What it does

- **Two ways in** — type or paste text directly, or upload a `.pdf`, `.txt`, `.csv`, or
  `.xlsx` file.
- **Translation** into any language in the registry, via Gemini 3.6 Flash, with automatic
  fallback to another model if that one is retired.
- **Speech** — the translated text is synthesized to MP3 with gTTS, played inline, and
  offered for download.
- **Honest limits** — a language Gemini can translate but gTTS cannot speak still
  translates; the app explains why there is no audio instead of failing.

---

## Setup

### 1. Requirements

Python 3.11–3.13 (see the note on 3.14 under [Limitations](#limitations)).

### 2. Install

```powershell
git clone <your-repo-url>
cd Capstone_Project

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On macOS or Linux, substitute `python3 -m venv .venv` and `source .venv/bin/activate`.

### 3. Get a Gemini API key

1. Open <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and choose or create a Google Cloud project.
3. Copy the key.

The key must belong to a project with the **Generative Language API** enabled. A key
created in the Google Cloud console works equally well provided that API is enabled and no
API restriction excludes it. A key scoped only to **Vertex AI** will *not* work here —
Vertex authenticates with service-account credentials and a project/location pair rather
than an API key.

### 4. Configure the key

Copy `.env.example` to `.env` and fill it in:

```
GEMINI_API_KEY=your-key-here
```

`.env` is listed in `.gitignore` and must never be committed.

Verify it before launching the app:

```powershell
.\.venv\Scripts\python.exe scripts\check_api_key.py
```

This makes one small live call and, on failure, names the specific cause.

### 5. Run

```powershell
streamlit run app.py
```

The app opens at <http://localhost:8501>.

---

## Usage

1. Choose a target language in the sidebar. The caption tells you whether that language has
   a voice available.
2. Enter text on the **Type or paste** tab, or upload a file on the **Upload a file** tab.
   Extracted text is previewed with a character count.
3. Press **Translate**. Long documents are split into chunks and translated in order, with
   a progress bar.
4. Read the result, and download it as `.txt` if you want it.
5. Press **Generate audio**, play it in the browser, and download the `.mp3`.

Sample files for trying each format are in `sample_files/`, including
`scanned_no_text.pdf`, which deliberately demonstrates the scanned-PDF failure message.

---

## Architecture

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

`app.py` contains no business logic. Streamlit re-executes the entire script on every
widget interaction, so anything expensive placed there runs on every click. Every external
call lives behind a plain function in `src/`, which also means the logic is unit-testable
without an API key or a network connection.

### Four decisions worth stating

**The language registry is the single source of truth.** Gemini translates into hundreds of
languages; gTTS speaks about 69, using its own short codes (`hi`, `ta`, `fr`). The two sets
are not the same. One dictionary in `src/languages.py` maps a display name to both a Gemini
target name and an optional gTTS code. When the code is `None`, translation still works and
the audio section explains itself. A test asserts every code in the registry is one gTTS
actually supports — an invalid code is otherwise invisible until synthesis fails at runtime.

**Results live in `st.session_state`.** Clicking `st.download_button` triggers a script
re-run. Without holding the translated text and audio bytes in session state, downloading
the MP3 would re-call Gemini and gTTS on every click — slower, and it burns API quota.

**Caching is keyed on content.** `@st.cache_data` wraps both the translation and the
synthesis, keyed on `(sha256(text), target)`. Re-translating identical input is free.

**Chunking preserves order.** Long documents exceed practical prompt sizes, so
`translator.py` splits on paragraph boundaries into ~3000-character chunks, translates
each, and rejoins in order. `tts.py` does the same at 1500 characters and concatenates the
resulting MP3 frames into one track.

### Layout

```
app.py                  Streamlit entry point — UI only
src/config.py           API key resolution, size and length limits
src/languages.py        language registry and lookup helpers
src/extractors.py       PDF / TXT / CSV / XLSX → plain text
src/translator.py       Gemini wrapper: prompt, chunking, retries, error mapping
src/tts.py              gTTS wrapper: text → MP3 bytes
scripts/check_api_key.py  one-call key verification
tests/                  57 unit tests, no network required
sample_files/           fixtures for the manual test matrix
```

---

## Testing

```powershell
.\.venv\Scripts\python.exe -m pytest              # whole suite
.\.venv\Scripts\python.exe -m pytest tests/test_translator.py -v
.\.venv\Scripts\python.exe -m pytest tests/test_translator.py::TestChunking -v
```

The suite passes with no API key configured and makes no network calls. Gemini is replaced
by a stub client that replays scripted responses and errors; gTTS is monkeypatched to write
a byte marker. The PDF fixtures are byte-level PDFs constructed in `tests/conftest.py`, so
no extra dependency is needed to test PDF extraction.

Coverage includes all four file formats, encoding fallback, the scanned-PDF and oversized
-file refusals, chunk ordering and content preservation, the retry-then-explain path for
rate limits, non-retry for bad keys, and the translate-but-cannot-speak case.

---

## Deployment

Deployed on **Streamlit Community Cloud**:

1. Push the repository to GitHub (public).
2. At <https://share.streamlit.io>, create an app pointing at `app.py`.
3. In **Advanced settings → Secrets**, add:
   ```toml
   GEMINI_API_KEY = "your-key-here"
   ```
4. Deploy, then test each file format and several languages on the live URL.

`runtime.txt` pins Python 3.13 because Streamlit Cloud does not yet offer 3.14.

The project brief names Heroku as a deployment option. Heroku discontinued its free dyno
tier in November 2022, so Streamlit Community Cloud was substituted — it is free and
purpose-built for this stack.

---

## Limitations

**Translation quality cannot be fully verified.** Output was spot-checked by round-trip
translation, but no native speaker reviewed the non-English results. Gemini is strong at
common language pairs and weaker at low-resource ones.

**Gemini free-tier rate limits.** Limits are per minute and per day, and Google revises
them, so the current values are worth checking in AI Studio rather than trusting a number
written here. A long document becomes many chunks fired in quick succession, which is
exactly the shape that hits a per-minute limit — this was reproduced during testing on a
4,800-character document. Mitigated three ways: a 20,000-character cap per translation so
one upload cannot consume the daily quota; retries that honour the `RetryInfo` delay the
API returns rather than guessing; and a fallback to a lighter model.

**Models get retired.** `gemini-2.5-flash` was the original choice and became unavailable
to new API keys during development, returning 404. `src/config.py` now names a current
model plus a fallback chain, and the translator advances through it on 404. A deployment
left running for months may still eventually need `MODEL_NAME` updated.

**Free-tier inputs may be used to improve Google's models.** The paid tier excludes this;
the free tier does not. For a translation tool this matters, since users may paste
sensitive documents. The app carries a visible warning against uploading confidential
material.

**gTTS is an unofficial client** for Google Translate's speech endpoint. It has no API key,
is rate-limited by IP, and can break without notice — on shared cloud hosting it
occasionally returns HTTP 429. Caught explicitly and shown as a retry message.

**About 69 languages have a voice**, fewer than Gemini can translate. Punjabi, Amharic and
others do have voices; Persian, Odia, Assamese, Irish and Lao do not. Those translate
normally but produce no audio.

**Scanned PDFs cannot be read.** pypdf extracts embedded text only. Image-only pages yield
nothing, and the app says so rather than translating an empty string. OCR is out of scope.

**Voice quality is uniform.** gTTS offers one voice per language; `tld` varies the accent
(for example British versus American English) but not the speaker.

**Python 3.14 locally, 3.13 deployed.** Everything resolves on 3.14, but Streamlit Cloud
caps at 3.13, so `runtime.txt` pins the lower version.

---

## Challenges faced

**Reconciling two different language systems.** The first real design problem was that
Gemini and gTTS do not agree on what languages exist. Gemini takes a language *name* in a
prompt and translates into almost anything; gTTS takes a short *code* from a fixed list of
about 69. Building that mapping ad hoc in the UI is the obvious approach and the wrong one —
it puts the mismatch in the place least able to handle it. Centralising it in
`languages.py`, with `None` as an explicit "no voice" value, turned an unpredictable
runtime crash into a designed-for message. Writing a test that checks every code against
`gtts.lang.tts_langs()` caught two genuine errors immediately: Punjabi and Amharic had been
marked as having no voice when gTTS supports both.

**Streamlit's re-run model.** Streamlit re-executes the whole script on every interaction,
which quietly breaks the obvious implementation: clicking "download MP3" re-runs the script,
re-calls Gemini and gTTS, and burns quota on a click that should cost nothing. The fix —
compute on button press, store in `st.session_state`, read everywhere else — is simple once
understood but is not what the framework's tutorial style suggests.

**Chunking without losing order or content.** Splitting a long document is easy; splitting
it so that nothing is lost, nothing is duplicated, and the pieces rejoin in the original
order is fiddlier. Paragraphs longer than the cap need a sentence-level fallback, and a
single sentence longer than the cap needs a hard split. Tests assert content preservation
and ordering independently, because a chunker can easily satisfy one and violate the other.

**Making failures legible.** The SDK raises typed errors with HTTP codes; a user needs to
know whether to wait a minute, fix their key, or shorten their text. Mapping code 429 to a
rate-limit explanation, 401/403 to a key-setup message, and 5xx to "try again shortly" —
and retrying only the transient ones — was more code than the happy path, and is most of
what makes the app usable.

**Testing PDF extraction without a PDF library that writes PDFs.** pypdf reads but does not
easily author text-bearing PDFs, and adding reportlab purely for tests was unappealing.
Constructing a minimal PDF byte-by-byte in `conftest.py`, with correct cross-reference table
offsets, kept the test suite dependency-free and made the scanned-PDF case (a page object
with no content stream) trivial to represent.

**A key that looks right but isn't.** Getting a working API key took three attempts, and
each failure looked similar from the outside. The first key came from Google Cloud's Agent
Platform console; it starts with `AQ.` rather than `AIzaSy` and is rejected outright,
because Agent Platform is a Vertex AI surface that authenticates with service-account
credentials and a project/location pair, not an API key. The Gemini Developer API used
here is a different front door to the same models. The second question — whether the
Generative Language API was even enabled on the project — turned out to be a red herring;
the error text `Requests to this API ... are blocked` proves the API is enabled and the
*key* is the problem, a distinction that is easy to miss. `scripts/check_api_key.py` exists
because a bare 403 does not tell you which of those things went wrong: it probes with a
model listing (which costs no generation quota), pattern-matches the error, and prints the
specific fix.

**A model disappearing mid-project.** `gemini-2.5-flash` appeared in the model listing but
returned 404 on generation, with the message that it is "no longer available to new users"
— an existing key would have kept working, but a newly created one is a new user. The fix
was to probe the candidate models directly rather than trust documentation, which also
revealed that `thinking_budget=0` — a sensible optimisation for translation, since it needs
no reasoning tokens — is rejected as an invalid argument by Gemini 3 models, and that
`gemini-2.0-flash` was already quota-exhausted. Measuring beat guessing: the chosen model
translated in 2.15s against 11.36s for another candidate.

---

## License

Submitted as coursework for the Generative AI and ML capstone.

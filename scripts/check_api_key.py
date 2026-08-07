"""Verify the configured Gemini API key and report exactly what is wrong if it fails.

Run:  .venv\\Scripts\\python.exe scripts\\check_api_key.py

Exists because a key can be present but unusable in several distinct ways — the
Generative Language API not enabled on its project, an API restriction excluding it, or a
key scoped to Vertex AI (which authenticates with service-account credentials, not an
api_key). All three surface as a bare 403, so this separates them.

Step 1 lists models over plain REST. That call consumes no generation quota and its error
body names a disabled API explicitly, including the URL to enable it. Step 2 runs a real
two-word translation through the app's own code path.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import MODEL_NAME, get_api_key  # noqa: E402
from src.translator import TranslationError, translate  # noqa: E402

_LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models?key={key}"


def probe_api(key: str) -> tuple[bool, str]:
    """List models. Returns (ok, detail) — detail is the API's own message on failure."""
    try:
        with urllib.request.urlopen(_LIST_URL.format(key=key), timeout=30) as response:
            payload = json.load(response)
        names = [m.get("name", "") for m in payload.get("models", [])]
        return True, f"{len(names)} models visible"
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return False, body.get("error", {}).get("message", str(exc))
        except Exception:
            return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"could not reach the API: {exc}"


def check_format(key: str) -> None:
    """Warn about a key whose shape is wrong before spending a network call on it.

    Gemini API keys are Google API keys: 'AIzaSy' + 33 characters. Keys issued by Vertex AI
    / Agent Platform are longer and start with 'AQ.', and are rejected by this API.
    """
    if key.startswith("AQ."):
        print("WARN  This looks like a Vertex AI / Agent Platform key, not a Gemini API key.")
        print("      Those authenticate differently and cannot call this API.")
        print("      Create one at https://aistudio.google.com/apikey — it starts 'AIzaSy'.\n")
    elif not key.startswith("AIzaSy"):
        print("WARN  Gemini API keys normally start with 'AIzaSy'. This one does not.\n")


def explain(detail: str) -> None:
    lowered = detail.lower()
    if "are blocked" in lowered or "blocked" in lowered:
        print("      The API is enabled, but this key is not permitted to call it.")
        print("      Two causes, in order of likelihood:")
        print("      1. The key is a Vertex AI / Agent Platform key. Those cannot call the")
        print("         Gemini Developer API at all. Create a proper Gemini API key at")
        print("         https://aistudio.google.com/apikey (it will start with 'AIzaSy').")
        print("      2. The key has an API restriction excluding Generative Language. Open")
        print("         it at https://console.cloud.google.com/apis/credentials and set")
        print("         'API restrictions' to allow Generative Language API.")
    elif "has not been used in project" in lowered or "is disabled" in lowered:
        print("      The Generative Language API is NOT enabled on this key's project.")
        print("      Enable it here, wait a minute, then re-run this script:")
        print("      https://console.cloud.google.com/apis/library/"
              "generativelanguage.googleapis.com")
        print("      (the error message above also contains a direct activation link)")
    elif "api key not valid" in lowered or "api_key_invalid" in lowered:
        print("      The key itself is not valid for this API. Most likely it is scoped to")
        print("      Vertex AI / Agent Platform, which uses service-account credentials")
        print("      rather than an api_key. Create a Gemini API key instead:")
        print("      https://aistudio.google.com/apikey")
    elif "permission" in lowered or "forbidden" in lowered:
        print("      The key exists but is restricted. In the Cloud console, open the key's")
        print("      settings and check 'API restrictions' — either allow Generative")
        print("      Language API, or set it to 'Don't restrict key'.")
    elif "referer" in lowered or "referrer" in lowered or "ip address" in lowered:
        print("      The key has an application restriction (HTTP referrer or IP allowlist)")
        print("      that blocks calls from this machine. Remove it for local development.")


def main() -> int:
    key = get_api_key()
    if not key:
        print("FAIL  No key found.")
        print("      Uncomment the GEMINI_API_KEY line in .env and paste your key after '='.")
        return 1

    print(f"Key found: {key[:6]}…{key[-4:]}  ({len(key)} chars)\n")
    check_format(key)

    print("[1/2] Checking the Generative Language API is enabled and the key is accepted…")
    ok, detail = probe_api(key)
    if not ok:
        print(f"FAIL  {detail}\n")
        explain(detail)
        return 1
    print(f"      OK — {detail}\n")

    print(f"[2/2] Translating a test phrase with {MODEL_NAME}…")
    try:
        result = translate("Good morning", "Hindi")
    except TranslationError as exc:
        print(f"FAIL  {exc}")
        return 1

    print(f"      OK — Gemini replied: {result!r}\n")
    print("Everything works. Start the app with:")
    print("      .venv\\Scripts\\streamlit.exe run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The language registry — the single source of truth for what this app offers.

Gemini translates into far more languages than gTTS can speak, and gTTS uses its own short
codes. Keeping one mapping here means the UI never has to reconcile the two sets: a language
with `tts_code=None` still translates, and the audio section explains why it cannot speak.

`tld` selects a regional Google Translate host for accent (e.g. co.uk for British English).
It only affects voice, never the translation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    """One offered language.

    Attributes:
        gemini_name: The name written into the translation prompt.
        tts_code: gTTS language code, or None when gTTS cannot speak this language.
        tld: Google Translate host suffix controlling accent.
    """

    gemini_name: str
    tts_code: str | None
    tld: str = "com"

    @property
    def speakable(self) -> bool:
        return self.tts_code is not None


# Display name -> Language. Ordered roughly by expected use; the UI preserves this order.
LANGUAGES: dict[str, Language] = {
    # --- Indian languages -------------------------------------------------------
    "Hindi": Language("Hindi", "hi", "co.in"),
    "Tamil": Language("Tamil", "ta", "co.in"),
    "Telugu": Language("Telugu", "te", "co.in"),
    "Kannada": Language("Kannada", "kn", "co.in"),
    "Malayalam": Language("Malayalam", "ml", "co.in"),
    "Marathi": Language("Marathi", "mr", "co.in"),
    "Gujarati": Language("Gujarati", "gu", "co.in"),
    "Bengali": Language("Bengali", "bn", "co.in"),
    "Urdu": Language("Urdu", "ur", "co.in"),
    "Nepali": Language("Nepali", "ne", "co.in"),
    "Sinhala": Language("Sinhala", "si"),
    "Punjabi": Language("Punjabi", "pa", "co.in"),
    "Odia": Language("Odia (Oriya)", None),
    "Assamese": Language("Assamese", None),
    # --- European ---------------------------------------------------------------
    "English (US)": Language("English", "en", "com"),
    "English (UK)": Language("English", "en", "co.uk"),
    "French": Language("French", "fr", "fr"),
    "Spanish": Language("Spanish", "es", "es"),
    "Portuguese (Brazil)": Language("Brazilian Portuguese", "pt", "com.br"),
    "German": Language("German", "de"),
    "Italian": Language("Italian", "it"),
    "Dutch": Language("Dutch", "nl"),
    "Polish": Language("Polish", "pl"),
    "Russian": Language("Russian", "ru"),
    "Ukrainian": Language("Ukrainian", "uk"),
    "Greek": Language("Greek", "el"),
    "Czech": Language("Czech", "cs"),
    "Swedish": Language("Swedish", "sv"),
    "Danish": Language("Danish", "da"),
    "Finnish": Language("Finnish", "fi"),
    "Norwegian": Language("Norwegian", "no"),
    "Hungarian": Language("Hungarian", "hu"),
    "Romanian": Language("Romanian", "ro"),
    "Turkish": Language("Turkish", "tr"),
    "Irish": Language("Irish (Gaeilge)", None),
    "Icelandic": Language("Icelandic", "is"),
    # --- Middle East / Africa ---------------------------------------------------
    "Arabic": Language("Arabic", "ar"),
    "Hebrew": Language("Hebrew", "iw"),
    "Persian": Language("Persian (Farsi)", None),
    "Swahili": Language("Swahili", "sw"),
    "Afrikaans": Language("Afrikaans", "af"),
    "Amharic": Language("Amharic", "am"),
    # --- East / Southeast Asia --------------------------------------------------
    "Chinese (Simplified)": Language("Simplified Chinese", "zh-CN"),
    "Chinese (Traditional)": Language("Traditional Chinese", "zh-TW"),
    "Japanese": Language("Japanese", "ja"),
    "Korean": Language("Korean", "ko"),
    "Vietnamese": Language("Vietnamese", "vi"),
    "Thai": Language("Thai", "th"),
    "Indonesian": Language("Indonesian", "id"),
    "Filipino": Language("Filipino (Tagalog)", "tl"),
    "Malay": Language("Malay", "ms"),
    "Khmer": Language("Khmer", "km"),
    "Burmese": Language("Burmese", "my"),
    "Lao": Language("Lao", None),
}

#: Languages written right-to-left — the UI renders their output with dir="rtl".
RTL_DISPLAY_NAMES = frozenset({"Arabic", "Hebrew", "Urdu", "Persian"})


def supported_languages() -> list[str]:
    """Display names in registry order, for the UI dropdown."""
    return list(LANGUAGES)


def get_language(display_name: str) -> Language:
    """Look up a language by display name.

    Raises:
        KeyError: with a readable message if the name is not in the registry.
    """
    try:
        return LANGUAGES[display_name]
    except KeyError:
        raise KeyError(f"Unsupported language: {display_name!r}") from None


def gemini_name_for(display_name: str) -> str:
    return get_language(display_name).gemini_name


def tts_code_for(display_name: str) -> str | None:
    """gTTS code, or None when this language has no voice. Callers must handle None."""
    return get_language(display_name).tts_code


def tld_for(display_name: str) -> str:
    return get_language(display_name).tld


def is_speakable(display_name: str) -> bool:
    return get_language(display_name).speakable


def is_rtl(display_name: str) -> bool:
    return display_name in RTL_DISPLAY_NAMES

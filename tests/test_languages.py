import pytest
from gtts.lang import tts_langs

from src import languages


def test_registry_is_not_empty():
    assert len(languages.supported_languages()) >= 40


def test_every_language_has_a_gemini_name():
    for name, language in languages.LANGUAGES.items():
        assert language.gemini_name.strip(), f"{name} has no Gemini target name"


def test_every_tts_code_is_one_gtts_actually_supports():
    """The bug this guards against is silent: an invalid code only fails at synthesis time."""
    supported = set(tts_langs())
    invalid = {
        name: language.tts_code
        for name, language in languages.LANGUAGES.items()
        if language.tts_code is not None and language.tts_code not in supported
    }
    assert not invalid, f"codes gTTS does not support: {invalid}"


def test_registry_contains_both_speakable_and_unspeakable_languages():
    speakable = [n for n in languages.LANGUAGES if languages.is_speakable(n)]
    silent = [n for n in languages.LANGUAGES if not languages.is_speakable(n)]
    assert speakable and silent, "the translate-but-cannot-speak path must stay exercised"


def test_unspeakable_language_returns_none_rather_than_raising():
    silent = next(n for n in languages.LANGUAGES if not languages.is_speakable(n))
    assert languages.tts_code_for(silent) is None
    assert languages.gemini_name_for(silent)  # translation still works


def test_unknown_language_raises_readable_keyerror():
    with pytest.raises(KeyError, match="Klingon"):
        languages.get_language("Klingon")


def test_rtl_names_all_exist_in_the_registry():
    assert languages.RTL_DISPLAY_NAMES <= set(languages.LANGUAGES)


def test_rtl_detection():
    assert languages.is_rtl("Arabic")
    assert not languages.is_rtl("French")


def test_regional_variants_share_a_code_but_differ_by_accent():
    assert languages.tts_code_for("English (UK)") == languages.tts_code_for("English (US)")
    assert languages.tld_for("English (UK)") != languages.tld_for("English (US)")


def test_dropdown_order_is_stable():
    assert languages.supported_languages() == list(languages.LANGUAGES)

"""TTS tests. gTTS is stubbed — the suite makes no network call."""

from __future__ import annotations

import pytest
from gtts.tts import gTTSError

from src import tts
from src.tts import TTSError, split_for_speech, synthesize


class FakeGTTS:
    """Stands in for gTTS, writing a recognisable byte marker instead of real audio."""

    instances = []

    def __init__(self, text, lang, tld="com", slow=False):
        self.text = text
        self.lang = lang
        self.tld = tld
        FakeGTTS.instances.append(self)

    def write_to_fp(self, fp):
        fp.write(b"MP3:" + self.text.encode("utf-8"))


@pytest.fixture
def fake_gtts(monkeypatch):
    FakeGTTS.instances = []
    monkeypatch.setattr(tts, "gTTS", FakeGTTS)
    return FakeGTTS


class TestSplitting:
    def test_short_text_is_one_piece(self):
        assert split_for_speech("Hello there", 1500) == ["Hello there"]

    def test_every_piece_respects_the_cap(self):
        text = "\n".join(f"Line number {i} with several words in it." for i in range(60))
        assert all(len(p) <= 100 for p in split_for_speech(text, 100))

    def test_order_is_preserved(self):
        text = "\n".join(f"MARKER{i}" for i in range(20))
        pieces = split_for_speech(text, 40)
        found = [next(i for i, p in enumerate(pieces) if f"MARKER{n}" in p) for n in range(20)]
        assert found == sorted(found)

    def test_over_long_line_is_hard_split(self):
        assert len(split_for_speech("y" * 450, 100)) == 5

    def test_empty_input_produces_nothing(self):
        assert split_for_speech("  \n \n ", 100) == []


class TestSynthesize:
    def test_returns_mp3_bytes(self, fake_gtts):
        assert synthesize("Hello", "en") == b"MP3:Hello"

    def test_long_text_is_concatenated_in_order(self, fake_gtts):
        # Long enough to exceed the real TTS_CHUNK_CHARS default and force several calls.
        text = "\n".join(f"MARKER{i}" for i in range(300))
        audio = synthesize(text, "en").decode("utf-8")
        assert len(fake_gtts.instances) > 1
        assert audio.index("MARKER0") < audio.index("MARKER299")

    def test_accent_tld_is_passed_through(self, fake_gtts):
        synthesize("Hello", "en", tld="co.uk")
        assert fake_gtts.instances[0].tld == "co.uk"

    def test_language_without_a_voice_explains_translation_is_unaffected(self):
        with pytest.raises(TTSError, match="translation above is still correct"):
            synthesize("some text", None)

    def test_empty_text_rejected(self, fake_gtts):
        with pytest.raises(TTSError, match="no text"):
            synthesize("   ", "en")

    def test_rate_limit_gets_a_retry_message(self, monkeypatch):
        def boom(*args, **kwargs):
            raise gTTSError("429 (Too Many Requests) from TTS API")

        monkeypatch.setattr(tts, "gTTS", boom)
        with pytest.raises(TTSError, match="rate-limiting"):
            synthesize("Hello", "en")

    def test_unknown_code_is_reported_clearly(self, monkeypatch):
        def boom(*args, **kwargs):
            raise ValueError("Language not supported: xx")

        monkeypatch.setattr(tts, "gTTS", boom)
        with pytest.raises(TTSError, match="not a language"):
            synthesize("Hello", "xx")

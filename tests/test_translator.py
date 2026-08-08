"""Translator tests. A stub client stands in for Gemini — no key, no network."""

from __future__ import annotations

import pytest
from google.genai import errors

from src.config import FALLBACK_MODELS, MAX_INPUT_CHARS, MAX_RETRIES, MODEL_NAME
from src.translator import TranslationError, split_into_chunks, translate


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    """Records calls and replays a scripted sequence of results or exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def rate_limited():
    """A per-minute 429: no QuotaFailure detail naming a PerDay quota."""
    return errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})


def daily_quota_exhausted():
    """The 429 the free tier actually returns once a model's daily allowance is gone."""
    return errors.ClientError(
        429,
        {
            "error": {
                "message": "quota exceeded",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                "quotaValue": "20",
                            }
                        ],
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "37s",
                    },
                ],
            }
        },
    )


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert split_into_chunks("A short sentence.", 3000) == ["A short sentence."]

    def test_every_chunk_respects_the_cap(self):
        text = "\n\n".join(f"Paragraph {i}. " + "word " * 40 for i in range(30))
        assert all(len(c) <= 200 for c in split_into_chunks(text, 200))

    def test_no_content_is_lost(self):
        text = "\n\n".join(f"Paragraph number {i} with some words." for i in range(20))
        rejoined = "".join(split_into_chunks(text, 120)).replace("\n", "").replace(" ", "")
        assert rejoined == text.replace("\n", "").replace(" ", "")

    def test_order_is_preserved(self):
        text = "\n\n".join(f"MARKER{i}" for i in range(15))
        chunks = split_into_chunks(text, 40)
        positions = [next(i for i, c in enumerate(chunks) if f"MARKER{n}" in c) for n in range(15)]
        assert positions == sorted(positions)

    def test_single_sentence_longer_than_the_cap_is_hard_split(self):
        chunks = split_into_chunks("x" * 500, 100)
        assert len(chunks) == 5
        assert all(len(c) <= 100 for c in chunks)

    def test_empty_input_produces_no_chunks(self):
        assert split_into_chunks("", 100) == []
        assert split_into_chunks("   \n\n  ", 100) == []

    def test_invalid_cap_rejected(self):
        with pytest.raises(ValueError):
            split_into_chunks("text", 0)


class TestTranslate:
    def test_returns_the_model_output(self):
        client = FakeClient(["नमस्ते"])
        assert translate("Hello", "Hindi", client=client) == "नमस्ते"

    def test_one_api_call_per_chunk_and_results_rejoined_in_order(self):
        client = FakeClient(["FIRST", "SECOND", "THIRD"])
        # Each paragraph fits in one chunk alone but no two fit together, so this is
        # exactly three chunks under the real TRANSLATION_CHUNK_CHARS default.
        text = "\n\n".join("word " * 500 for _ in range(3))
        result = translate(text, "Tamil", client=client)
        assert len(client.models.calls) == 3
        assert result.index("FIRST") < result.index("SECOND") < result.index("THIRD")

    def test_target_language_reaches_the_prompt(self):
        client = FakeClient(["ok"])
        translate("Hello", "Tamil", client=client)
        assert "Tamil" in client.models.calls[0]["config"].system_instruction

    def test_progress_callback_reports_each_chunk(self):
        client = FakeClient(["a", "b"])
        seen = []
        translate("\n\n".join("word " * 500 for _ in range(2)), "Hindi",
                  client=client, progress_callback=lambda done, total: seen.append((done, total)))
        assert seen[-1][0] == seen[-1][1]

    def test_empty_input_rejected_before_any_api_call(self):
        client = FakeClient(["never"])
        with pytest.raises(TranslationError, match="nothing to translate"):
            translate("   ", "Hindi", client=client)
        assert client.models.calls == []

    def test_input_over_the_cap_is_rejected_before_any_api_call(self):
        client = FakeClient(["never"])
        with pytest.raises(TranslationError, match="character limit|over the"):
            translate("x" * (MAX_INPUT_CHARS + 1), "Hindi", client=client)
        assert client.models.calls == []

    def test_missing_api_key_gives_a_setup_message(self, no_api_key):
        with pytest.raises(TranslationError, match="GEMINI_API_KEY"):
            translate("Hello", "Hindi")


class TestErrorHandling:
    def test_rate_limit_is_retried_then_explained(self):
        client = FakeClient([rate_limited()])
        with pytest.raises(TranslationError, match="rate limit"):
            translate("Hello", "Hindi", client=client)
        assert len(client.models.calls) == MAX_RETRIES

    def test_transient_failure_recovers_without_surfacing(self):
        client = FakeClient([rate_limited(), "recovered"])
        assert translate("Hello", "Hindi", client=client) == "recovered"
        assert len(client.models.calls) == 2

    def test_bad_key_is_not_retried(self):
        client = FakeClient([errors.ClientError(403, {"error": {"message": "denied"}})])
        with pytest.raises(TranslationError, match="API key"):
            translate("Hello", "Hindi", client=client)
        assert len(client.models.calls) == 1

    def test_server_error_maps_to_try_again(self):
        client = FakeClient([errors.ServerError(503, {"error": {"message": "unavailable"}})])
        with pytest.raises(TranslationError, match="temporarily unavailable"):
            translate("Hello", "Hindi", client=client)

    def test_blocked_response_is_explained_not_retried(self):
        client = FakeClient([""])
        with pytest.raises(TranslationError, match="safety filter"):
            translate("Hello", "Hindi", client=client)
        assert len(client.models.calls) == 1

    def test_server_supplied_retry_delay_is_honoured(self, monkeypatch):
        """Free-tier limits are per-minute; the API says when to come back, so obey it."""
        slept = []
        monkeypatch.setattr("time.sleep", slept.append)
        quota_error = errors.ClientError(
            429,
            {
                "error": {
                    "message": "quota",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "27s",
                        }
                    ],
                }
            },
        )
        client = FakeClient([quota_error, "ok"])
        assert translate("Hello", "Hindi", client=client) == "ok"
        assert slept == [27.0]

    def test_retry_delay_is_capped_so_the_ui_cannot_hang(self, monkeypatch):
        slept = []
        monkeypatch.setattr("time.sleep", slept.append)
        client = FakeClient([
            errors.ClientError(429, {"error": {"message": "quota", "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3600s"}
            ]}}),
            "ok",
        ])
        translate("Hello", "Hindi", client=client)
        assert slept == [45.0]

    def test_missing_retry_info_falls_back_to_exponential_backoff(self, monkeypatch):
        slept = []
        monkeypatch.setattr("time.sleep", slept.append)
        client = FakeClient([rate_limited(), "ok"])
        translate("Hello", "Hindi", client=client)
        assert slept == [8.0]

    def test_daily_quota_switches_model_instead_of_sleeping(self, monkeypatch):
        """The daily cap is per model, so the fix is a different model, not waiting."""
        slept = []
        monkeypatch.setattr("time.sleep", slept.append)
        client = FakeClient([daily_quota_exhausted(), "ok"])
        assert translate("Hello", "Hindi", client=client) == "ok"
        assert slept == [], "sleeping cannot clear a daily quota"
        assert [c["model"] for c in client.models.calls] == [MODEL_NAME, FALLBACK_MODELS[0]]

    def test_daily_quota_does_not_waste_retries_on_the_same_model(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _s: None)
        client = FakeClient([daily_quota_exhausted()])
        with pytest.raises(TranslationError):
            translate("Hello", "Hindi", client=client)
        # One call per model in the chain — not MAX_RETRIES against each.
        assert len(client.models.calls) == 1 + len(FALLBACK_MODELS)

    def test_all_models_out_of_daily_quota_says_so_not_retired(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _s: None)
        client = FakeClient([daily_quota_exhausted()])
        with pytest.raises(TranslationError, match="free-tier requests for today"):
            translate("Hello", "Hindi", client=client)

    def test_daily_quota_message_does_not_tell_the_user_to_wait_a_minute(self, monkeypatch):
        """The misleading advice this replaced: RetryInfo says 37s, but it is a daily cap."""
        monkeypatch.setattr("time.sleep", lambda _s: None)
        client = FakeClient([daily_quota_exhausted()])
        with pytest.raises(TranslationError) as caught:
            translate("Hello", "Hindi", client=client)
        assert "minute" not in str(caught.value).lower()

    def test_per_minute_limit_still_sleeps_and_retries(self, monkeypatch):
        slept = []
        monkeypatch.setattr("time.sleep", slept.append)
        client = FakeClient([rate_limited(), "ok"])
        assert translate("Hello", "Hindi", client=client) == "ok"
        assert slept, "a per-minute limit does clear by waiting"
        assert [c["model"] for c in client.models.calls] == [MODEL_NAME, MODEL_NAME]

    def test_exhausted_model_is_skipped_for_later_chunks(self, monkeypatch):
        """A 10-chunk document must not re-try a dead model 10 times."""
        monkeypatch.setattr("time.sleep", lambda _s: None)
        # Chunk 1: primary 429s, fallback succeeds. Later chunks should skip the primary.
        client = FakeClient([daily_quota_exhausted(), "ok"])
        text = "\n\n".join("word " * 500 for _ in range(3))
        translate(text, "Hindi", client=client)

        models_tried = [c["model"] for c in client.models.calls]
        assert models_tried.count(MODEL_NAME) == 1, "dead model retried on later chunks"
        assert len(models_tried) == 4, "expected 1 failed + 3 successful calls"

    def test_availability_can_be_reset(self, monkeypatch):
        from src.translator import reset_model_availability

        monkeypatch.setattr("time.sleep", lambda _s: None)
        client = FakeClient([daily_quota_exhausted(), "ok"])
        translate("Hello", "Hindi", client=client)
        reset_model_availability()
        client2 = FakeClient(["ok"])
        translate("Hello again", "Hindi", client=client2)
        assert client2.models.calls[0]["model"] == MODEL_NAME

    def test_rate_limit_wait_is_announced_so_the_ui_is_not_silent(self):
        """A 45s wait with no feedback is indistinguishable from a hung app."""
        messages = []
        client = FakeClient([rate_limited(), "ok"])
        translate("Hello", "Hindi", client=client, status_callback=messages.append)
        assert len(messages) == 1
        assert "Rate limit" in messages[0] and "waiting" in messages[0]

    def test_no_status_callback_is_harmless(self):
        client = FakeClient([rate_limited(), "ok"])
        assert translate("Hello", "Hindi", client=client) == "ok"

    def test_retired_model_falls_through_to_the_next_in_the_chain(self):
        """Google retired gemini-2.5-flash mid-project; a 404 must not surface to the user."""
        client = FakeClient([errors.ClientError(404, {"error": {"message": "not found"}}), "ok"])
        assert translate("Hello", "Hindi", client=client) == "ok"
        models_tried = [call["model"] for call in client.models.calls]
        assert models_tried == [MODEL_NAME, FALLBACK_MODELS[0]]

    def test_retired_model_is_not_retried_against_itself(self):
        client = FakeClient([errors.ClientError(404, {"error": {"message": "not found"}}), "ok"])
        translate("Hello", "Hindi", client=client)
        assert client.models.calls[0]["model"] != client.models.calls[1]["model"]

    def test_every_model_retired_gives_an_actionable_message(self):
        client = FakeClient([errors.ClientError(404, {"error": {"message": "not found"}})])
        with pytest.raises(TranslationError, match="retired"):
            translate("Hello", "Hindi", client=client)
        assert len(client.models.calls) == 1 + len(FALLBACK_MODELS)

    def test_network_failure_is_retried_then_explained(self):
        client = FakeClient([ConnectionError("dns failure")])
        with pytest.raises(TranslationError, match="Could not reach"):
            translate("Hello", "Hindi", client=client)
        assert len(client.models.calls) == MAX_RETRIES

from types import SimpleNamespace

import pytest

from marketplace_bot.ai import FALLBACK_BETA, AIError, MatchAI
from marketplace_bot.models import ListingEvaluation, SearchPlan, TriageBatch, TriageItem

from .conftest import make_listing


class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed = parsed
        self.stop_reason = stop_reason
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=5
        )
        return SimpleNamespace(
            parsed_output=self.parsed,
            stop_reason=self.stop_reason,
            stop_details=SimpleNamespace(category="cyber") if self.stop_reason == "refusal" else None,
            usage=usage,
        )


def fake_client(parsed, stop_reason="end_turn"):
    messages = FakeMessages(parsed, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_request_uses_structured_output_effort_and_refusal_fallback(plan):
    client, messages = fake_client(plan)
    ai = MatchAI(client, "claude-opus-5-5")

    assert ai.plan("a desk near Austin", "Austin, TX", 25) is plan

    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is SearchPlan
    assert call["output_config"] == {"effort": "medium"}
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"
    assert "thinking" not in call
    assert "a desk near Austin" in call["messages"][0]["content"]
    assert ai.usage.requests == 1 and ai.usage.cache_read_tokens == 5


def test_fallback_can_be_turned_off(plan):
    client, messages = fake_client(plan)
    MatchAI(client, "claude-opus-5-5", refusal_fallback=False).plan("x", "", None)
    assert "fallbacks" not in messages.calls[0] and "betas" not in messages.calls[0]


def test_refusal_and_truncation_raise(plan):
    client, _ = fake_client(plan, stop_reason="refusal")
    with pytest.raises(AIError, match="declined"):
        MatchAI(client, "m").plan("x", "", None)
    client, _ = fake_client(plan, stop_reason="max_tokens")
    with pytest.raises(AIError, match="cut off"):
        MatchAI(client, "m").plan("x", "", None)


def test_triage_drops_unknown_ids_and_clamps_scores(plan):
    batch = TriageBatch(items=[TriageItem(id="1", score=14, reason="r"), TriageItem(id="999", score=3, reason="r")])
    client, messages = fake_client(batch)
    result = MatchAI(client, "m").triage("desk", plan, [make_listing("1", "Desk", 10, "Austin, TX")])
    assert result == {"1": (10, "r")}
    assert messages.calls[0]["output_config"] == {"effort": "low"}


def test_evaluate_sends_photos_before_text(plan):
    evaluation = ListingEvaluation(
        match_score=80, verdict="strong", summary="s", matched_requirements=[], missing_or_unclear=[],
        red_flags=[], questions_for_seller=[], price_assessment="p",
    )
    client, messages = fake_client(evaluation)
    listing = make_listing("1", "Desk", 10, "Austin, TX")
    listing.description = "Barely used <b>desk</b>"

    MatchAI(client, "m").evaluate("desk", plan, listing, [("image/jpeg", b"abc")])

    content = messages.calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "YWJj"}}
    assert content[1]["type"] == "text" and "Barely used <b>desk</b>" in content[1]["text"]

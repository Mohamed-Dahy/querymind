"""The follow-up conversations (3.6) load, and each one really is a follow-up."""

from evals.followups import load_followups
from evals.golden_set import GOLDEN_DIR, load_golden_set

FOLLOWUPS = load_followups()


def test_ten_conversations_with_unique_ids() -> None:
    ids = [c.id for c in FOLLOWUPS.conversations]
    assert len(ids) == 10 and len(set(ids)) == 10
    golden = {
        q.id
        for n in ("northwind.yaml", "chinook.yaml")
        for q in load_golden_set(GOLDEN_DIR / n).questions
    }
    assert not set(ids) & golden


def test_every_conversation_has_history_and_a_short_follow_up() -> None:
    for conversation in FOLLOWUPS.conversations:
        assert len(conversation.turns) >= 2, conversation.id
        # A follow-up leans on the earlier turns: short, not a full question on its own.
        assert len(conversation.last.question.split()) <= 10, conversation.id

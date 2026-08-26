import pytest

from app.errors import ContextTooLongError
from app.services.context import ContextBuilder


def test_system_prompt_survives_truncation():
    builder = ContextBuilder(max_messages=2, max_input_tokens=10_000)
    history = [("user", f"питання {i}") for i in range(10)]
    built = builder.build("ти асистент", history, "нове", "gpt-4o-mini")

    assert built.messages[0] == {"role": "system", "content": "ти асистент"}
    assert built.messages[-1] == {"role": "user", "content": "нове"}
    assert built.truncated is True
    assert len(built.messages) == 4  # system + 2 з історії + новий


def test_order_is_preserved():
    builder = ContextBuilder(max_messages=10, max_input_tokens=10_000)
    history = [("user", "перше"), ("assistant", "друге"), ("user", "третє")]
    built = builder.build(None, history, "четверте", "gpt-4o-mini")

    assert [m["content"] for m in built.messages] == ["перше", "друге", "третє", "четверте"]
    assert built.truncated is False


def test_token_limit_drops_oldest_first():
    builder = ContextBuilder(max_messages=100, max_input_tokens=40)
    history = [("user", "довге повідомлення номер %d" % i) for i in range(20)]
    built = builder.build(None, history, "нове", "gpt-4o-mini")

    assert built.truncated is True
    assert built.estimated_prompt_tokens <= 40
    assert built.messages[-1]["content"] == "нове"


def test_context_too_long_when_nothing_left_to_trim():
    builder = ContextBuilder(max_messages=10, max_input_tokens=5)
    with pytest.raises(ContextTooLongError):
        builder.build("дуже довгий системний промпт " * 20, [], "питання", "gpt-4o-mini")

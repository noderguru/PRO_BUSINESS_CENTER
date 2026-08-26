"""Збірка контексту сесії у payload для моделі.

Нове повідомлення завжди йде разом з історією, а не саме по собі.
"""

import logging
from dataclasses import dataclass

import tiktoken

from app.errors import ContextTooLongError

log = logging.getLogger(__name__)

_TOKENS_PER_MESSAGE_OVERHEAD = 4  # службові токени ролі та роздільників


@dataclass
class BuiltContext:
    messages: list[dict]
    truncated: bool
    estimated_prompt_tokens: int


def _encoding_for(model: str):
    try:
        return tiktoken.encoding_for_model(model.split("/")[-1])
    except KeyError:
        # ponytail: невідома модель -> базова кодування; це лише оцінка перед обрізанням,
        # білінг усе одно рахується з usage провайдера
        return tiktoken.get_encoding("o200k_base")


def _estimate(messages: list[dict], model: str) -> int:
    enc = _encoding_for(model)
    return sum(len(enc.encode(m["content"])) + _TOKENS_PER_MESSAGE_OVERHEAD for m in messages)


class ContextBuilder:
    def __init__(self, max_messages: int, max_input_tokens: int):
        self.max_messages = max_messages
        self.max_input_tokens = max_input_tokens

    def build(
        self,
        system_prompt: str | None,
        history: list[tuple[str, str]],
        new_user_content: str,
        model: str,
    ) -> BuiltContext:
        """history — пари (role, content) у порядку seq, без нового повідомлення."""
        head = [{"role": "system", "content": system_prompt}] if system_prompt else []
        tail = [{"role": r, "content": c} for r, c in history][-self.max_messages:]
        new = [{"role": "user", "content": new_user_content}]

        truncated = len(history) > len(tail)

        # найстаріші пари відкидаються першими; system і новий запит не обрізаються ніколи
        while tail and _estimate(head + tail + new, model) > self.max_input_tokens:
            tail.pop(0)
            truncated = True

        messages = head + tail + new
        estimated = _estimate(messages, model)
        if estimated > self.max_input_tokens:
            # system-промпт і сам запит уже не влазять — обрізати нічого
            raise ContextTooLongError(
                details={"estimated_prompt_tokens": estimated, "limit": self.max_input_tokens}
            )
        if truncated:
            log.info(
                "context truncated model=%s kept=%d of %d estimated_tokens=%d",
                model, len(tail), len(history), estimated,
            )
        return BuiltContext(messages=messages, truncated=truncated, estimated_prompt_tokens=estimated)

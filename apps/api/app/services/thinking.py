"""When a model spends the whole answer budget on thinking.

A reasoning model returns its chain of thought as `completion_tokens` like any
other output, and an OpenAI-compatible `max_tokens` caps the two together. So a
ceiling sized for the answer — 400 tokens for a report outline, 860 for an
eight-slide deck — is a ceiling a reasoning model never reaches the answer
inside. The call comes back `200 OK`, `finish_reason: "length"`, and
`content: ""`.

Measured on this deployment's own gateway, one outline prompt:

    openai/gpt-5-nano      finish=length  content 0자     reasoning 1,152 tokens
    qwen/qwen3.7-flash     finish=length  content 0자     reasoning 1,200 tokens
    z-ai/glm-4.7-flash     finish=length  content 잘림     reasoning 1,031 tokens
    google/gemma-4-26b     finish=stop    content 완전     reasoning 0
    deepseek/deepseek-v4   finish=stop    content 완전     reasoning 338

Three of five, and the three are not the cheap ones — gemma-4-26b is cheaper
than both models that failed. Nothing in the product could tell the difference
between this and a model that had nothing to say, so it reported the one thing
it could think of: "요청을 조금 더 구체적으로 적어 주세요." A person who wrote a
perfectly clear request was told to write it again, and rewriting it could not
have helped.

The fix is to notice and ask again with room for both. Not to guess in advance
which models reason: a catalogue of sixty models changes weekly, providers turn
reasoning on by default in point releases, and a guess that is wrong in the
other direction pays for headroom nobody uses on every call.
"""

from __future__ import annotations

#: Beyond a model's own thinking, the answer still has to fit. Added on top of
#: what the first attempt is now known to spend thinking.
_HEADROOM = 700

#: What the re-ask allows for thinking.
#:
#: Raising `max_tokens` alone is not enough, because some models spend whatever
#: they are given. Measured on the real outline prompt:
#:
#:     openai/gpt-5-nano   max=860   추론 832    본문 0자
#:     openai/gpt-5-nano   max=3000  추론 2,944  본문 0자      ← 예산을 따라 늘었다
#:     qwen/qwen3.7-flash  max=3000  추론 2,582  본문 457자
#:     qwen/qwen3.7-flash  max=3000 + 이 상한  추론 400  본문 486자
#:
#: So the re-ask caps the thinking as well as raising the ceiling. Sent as
#: OpenRouter's `reasoning` field; a gateway that does not know it answers 400
#: and the caller falls back to the ceiling alone, which is still better than
#: the first attempt.
REASONING_CAP = {"max_tokens": 400}


def starved(payload: dict, asked: int) -> int:
    """A bigger ceiling worth re-asking with, or `0` if the answer is not the
    problem.

    `0` covers every ordinary outcome — a complete answer, an empty answer the
    model chose, a refusal — because in all of them asking again with more room
    would buy nothing and cost a call.
    """
    choices = payload.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    if choice.get("finish_reason") != "length":
        return 0
    message = choice.get("message") or {}
    if (message.get("content") or "").strip():
        # Truncated but not empty. The callers' parsers already read a partial
        # answer, and re-asking would throw away what did arrive.
        return 0
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    thought = int(details.get("reasoning_tokens") or 0)
    if not thought:
        # Out of room with nothing to show and no reasoning to blame: the
        # answer itself did not fit, which a bigger ceiling does fix.
        thought = int(usage.get("completion_tokens") or 0)
    return asked + thought + _HEADROOM if thought else 0

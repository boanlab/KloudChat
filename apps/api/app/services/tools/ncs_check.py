"""A structured NCS check; arithmetic evidence is not proof of model interpretation."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.services.ncs_submission import submitted_choice_from_request
from app.services.tools import arithmetic
from app.services.tools.base import Tool, ToolContext, ToolResult

_ARITHMETIC_FIELDS = ("expression", "choices", "submitted_choice", "decimal_places")
_CALCULATE_FIELDS = frozenset(("decision", *_ARITHMETIC_FIELDS))
_REASON_FIELDS = frozenset({"decision", "reason"})
_DECISIONS = ("calculate", "needs_input", "not_applicable")
_MAX_REASON_CHARS = 500
_SCOPE = (
    "계산 필요 여부의 분류와 식 작성은 모델의 판단입니다. "
    "이 도구는 제공된 식의 산술만 확인하며 문제 해석의 정확성을 보장하지 않습니다."
)
_NEEDS_INPUT = (
    "필요한 조건이 부족하여 정답이나 채점을 확정할 수 없습니다. 문제의 누락된 조건을 보완해 주세요."
)
_NON_UNIQUE = (
    "계산기에 입력된 식과 선지에서 유일한 정답을 확인하지 못했습니다. "
    "채점을 보류하고 문제 조건·계산식·단위·선지를 다시 확인해야 합니다."
)
_ERROR = json.dumps(
    {
        "error": "invalid_ncs_check",
        "arithmetic_verified": False,
        "message": (
            "문항 검산 입력이 올바르지 않습니다. calculate에는 식과 계산 옵션만, "
            "needs_input 또는 not_applicable에는 1~500자의 이유만 입력하세요."
        ),
    },
    ensure_ascii=False,
)


def _invalid() -> ToolResult:
    return ToolResult(content=_ERROR, detail="문항 검산 미완료", failed=True)


async def check_ncs_answer(arguments: dict[str, Any], ctx: ToolContext | None = None) -> ToolResult:
    if not isinstance(arguments, dict) or len(arguments) > len(_CALCULATE_FIELDS):
        return _invalid()
    decision = arguments.get("decision")
    if not isinstance(decision, str) or decision not in _DECISIONS:
        return _invalid()

    if decision == "calculate":
        if set(arguments) - _CALCULATE_FIELDS:
            return _invalid()
        calculation = {
            name: arguments[name]
            for name in _ARITHMETIC_FIELDS
            if name in arguments
            and name != "submitted_choice"
            and not (name in {"choices", "decimal_places"} and arguments[name] is None)
        }
        submitted = submitted_choice_from_request(ctx.request) if ctx is not None else None
        if submitted is not None:
            calculation["submitted_choice"] = submitted
        result = await arithmetic.calculate(calculation)
        data = {
            **json.loads(result.content),
            "decision": decision,
            "arithmetic_verified": not result.failed,
            "submission_status": "explicit" if submitted is not None else "not_provided",
            "scope": _SCOPE,
        }
        return ToolResult(
            content=json.dumps(data, ensure_ascii=False),
            detail="문항 검산 미완료" if result.failed else "문항 검산 완료",
            failed=result.failed,
            final_text=(
                _NON_UNIQUE if data.get("choice_status") in {"no_match", "ambiguous"} else None
            ),
        )

    reason = arguments.get("reason")
    if (
        set(arguments) - _REASON_FIELDS
        or not isinstance(reason, str)
        or not 0 < len(reason) <= _MAX_REASON_CHARS
        or not reason.strip()
    ):
        return _invalid()
    return ToolResult(
        content=json.dumps(
            {
                "decision": decision,
                "reason": reason.strip(),
                "arithmetic_verified": False,
                "scope": _SCOPE,
            },
            ensure_ascii=False,
        ),
        detail="문항 검산 미완료",
        final_text=_NEEDS_INPUT if decision == "needs_input" else None,
    )


_ARITHMETIC_PARAMETERS = deepcopy(arithmetic.CALCULATE.parameters["properties"])
del _ARITHMETIC_PARAMETERS["submitted_choice"]
for _optional in ("choices", "decimal_places"):
    _ARITHMETIC_PARAMETERS[_optional]["type"] = [
        _ARITHMETIC_PARAMETERS[_optional]["type"],
        "null",
    ]


CHECK_NCS_ANSWER = Tool(
    name="check_ncs_answer",
    description=(
        "NCS 답변 전에 문항의 검산 상태를 구조화합니다. "
        "수리 풀이·채점·출제는 calculate와 숫자 식을 사용하고 필요한 선지·제출 답을 함께 "
        "보내세요. 조건 누락·모순이면 needs_input, 계산이 필요 없는 문항이면 "
        "not_applicable로 이유만 보내세요. 숫자 식은 사칙연산·괄호만 허용하며 "
        "단위는 제외하고 퍼센트는 *100으로 계산하세요. 반올림은 문제에 명시된 경우에만 "
        "지정하세요. 모델의 문제 해석이나 식 작성 자체의 정확성은 보장하지 않습니다."
        "제출 답은 사용자의 현재 요청에서 확인한 선택만 채점하며, 모델이 만든 "
        "submitted_choice는 무시합니다. submission_status가 not_provided이면 "
        "사용자가 답을 제출했다고 말하거나 정오를 판정하지 마세요."
    ),
    parameters={
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": list(_DECISIONS),
                "description": "Use calculate for arithmetic, needs_input for missing conditions.",
            },
            **_ARITHMETIC_PARAMETERS,
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_REASON_CHARS,
                "description": "Required for non-calculation decisions; omit arithmetic fields.",
            },
        },
        "required": ["decision"],
        "additionalProperties": False,
    },
    run=check_ncs_answer,
    label="문항 검산 중",
    title="문항 검산",
    read_only=True,
    wants_context=True,
)

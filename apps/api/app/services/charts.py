"""A chart as a picture, drawn from code rather than by a picture model.

A picture model paints a chart: the bars are roughly the right height and the
axis numbers are whatever looked plausible. A chart is the one picture where
"roughly" is wrong. So the 차트 style asks a language model for matplotlib
code instead, runs it in the code sandbox, and takes the PNG it saved — the
same road paper-banana takes for its statistical plots. The code is kept
beside the picture, so the figure can be corrected and drawn again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services import settings_store, thinking

log = logging.getLogger(__name__)


class ChartError(RuntimeError):
    """Written for the person who asked."""


@dataclass(slots=True)
class Chart:
    png: bytes
    code: str
    input_tokens: int
    output_tokens: int


#: What the writer is told. The figure size follows the aspect that was asked
#: for; the Korean face is the one the sandbox has; the look is the restrained
#: publication style a picture model cannot hold to.
_PROMPT = """너는 matplotlib 로 차트를 그리는 파이썬 코드를 쓴다. 아래 요청의 차트 하나를 그리는
완전한 파이썬 스크립트를 출력하라.

규칙:
- **요청에 있는 수치만 쓴다.** 값·계열·이름을 지어내지 마라. 요청에 그릴 수치가 없으면 코드
  대신 한 줄로 `NO_DATA: 무엇이 필요한지` 라고만 답하라.
- 차트 종류는 요청이 말한 것을, 없으면 데이터에 맞는 것을 고른다(추이는 선, 비교는 막대,
  구성비는 누적 막대나 도넛, 상관은 산점도). 3D·파이의 폭발·그림자 같은 장식은 쓰지 않는다.
- 한글 글꼴: `plt.rcParams["font.family"] = "NanumGothic"` 과
  `plt.rcParams["axes.unicode_minus"] = False`.
- `fig, ax = plt.subplots(figsize=({width}, {height}), dpi=200)`. 위·오른쪽 spine 은 숨긴다.
  격자는 y 축에만 옅게. 제목은 요청이 준 것만, 축에는 단위를 적는다. 범례는 계열이 둘 이상일
  때만. 막대 위에 값을 적으면 읽기 쉬운 경우에만 적는다.
- 색은 절제해서: 기본 팔레트 `["#1e3a8a", "#0f766e", "#b45309", "#7c3aed", "#64748b"]` 에서
  순서대로 쓰고, 강조할 항목이 있으면 그것만 첫 색으로, 나머지는 `"#cbd5e1"` 로.
- 마지막에 `fig.savefig("chart.png", bbox_inches="tight", dpi=200)` 로 저장하고 `print("OK")`.
- `plt.show()` 는 쓰지 마라. 파일을 읽거나 네트워크에 접근하지 마라. 코드만 출력하고 설명·
  코드펜스는 붙이지 마라.

요청: {request}"""

_RETRY = """앞의 코드가 실행 중 오류를 냈다. 고쳐서 전체 코드를 다시 출력하라. 같은 규칙.

오류:
{error}

앞의 코드:
{code}"""

#: Figure size in inches by aspect. Wide enough that labels do not crowd.
_SIZES = {
    "16:9": (9.6, 5.4),
    "4:3": (8.0, 6.0),
    "3:2": (9.0, 6.0),
    "1:1": (6.5, 6.5),
    "9:16": (5.4, 9.6),
    "3:4": (6.0, 8.0),
}


async def draw(
    request: str,
    *,
    aspect: str,
    model: str,
    api_key: str,
    code: str | None = None,
) -> Chart:
    """The chart for `request`, or `ChartError`.

    `code` runs as given — somebody edited the script the last picture was
    drawn from and wants exactly that. Otherwise the model writes it, and a
    script that fails is handed back to the model once with its error.
    """
    width, height = _SIZES.get(aspect, _SIZES["16:9"])
    tokens = {"in": 0, "out": 0}
    if code is None:
        code = await _write(
            _PROMPT.format(width=width, height=height, request=request.strip()[:3000]),
            model,
            api_key,
            tokens,
        )
        if code.startswith("NO_DATA"):
            need = code.split(":", 1)[1].strip() if ":" in code else ""
            raise ChartError(
                "차트로 그릴 수치가 요청에 없습니다."
                + (f" 필요한 것: {need}" if need else " 값과 항목 이름을 적어 주세요.")
            )
    png, error = await _run(code)
    if png is None and error:
        log.info("chart code failed once, asking for a fix: %s", error[:200])
        code = await _write(_RETRY.format(error=error[:1500], code=code), model, api_key, tokens)
        png, error = await _run(code)
    if png is None:
        raise ChartError("차트 코드가 실행되지 않았습니다. " + (error or "")[:300])
    return Chart(png=png, code=code, input_tokens=tokens["in"], output_tokens=tokens["out"])


async def _write(prompt: str, model: str, api_key: str, tokens: dict[str, int]) -> str:
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.2,
                    "reasoning": thinking.NO_REASONING,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise ChartError("차트 코드를 쓰는 모델에 닿지 못했습니다.") from exc
    usage = payload.get("usage") or {}
    tokens["in"] += int(usage.get("prompt_tokens") or 0)
    tokens["out"] += int(usage.get("completion_tokens") or 0)
    text = str((payload.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    return _unfenced(text)


def _unfenced(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:python|py)?\n(.*?)```", text, re.S)
    return match.group(1).strip() if match else text


async def _run(code: str) -> tuple[bytes | None, str]:
    """`(png, "")` when the script saved its chart, else `(None, what went wrong)`."""
    backends = await settings_store.tools_config()
    if not backends.exec:
        raise ChartError("코드 실행이 설정되지 않아 차트를 그릴 수 없습니다.")
    root = backends.exec.rstrip("/")
    headers = {"x-api-key": settings.code_interpreter_api_key}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            response = await client.post(
                f"{root}/exec", headers=headers, json={"code": code, "lang": "py"}
            )
            response.raise_for_status()
            payload = response.json()
            stderr = str(payload.get("stderr") or "").strip()
            files = [
                f for f in (payload.get("files") or []) if str(f.get("name") or "").endswith(".png")
            ]
            if not files:
                return None, stderr or "chart.png 이 저장되지 않았습니다."
            picked = next((f for f in files if f.get("name") == "chart.png"), files[0])
            session_id = picked.get("session_id") or payload.get("session_id")
            blob = await client.get(f"{root}/download/{session_id}/{picked['id']}", headers=headers)
            blob.raise_for_status()
            return blob.content, ""
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return None, f"실행 서비스 오류: {exc}"


__all__ = ["Chart", "ChartError", "draw"]

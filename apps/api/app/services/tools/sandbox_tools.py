"""File work in the code sandbox: fixed skill scripts and model-written code.

`run_skill_script` runs a shipped package script against the chat's files, or one of the
person's own skill scripts (`Skill.script_files`) — those only ever run here, in the sandbox.
`run_code` has the code model write Python for a task, runs it, repairs it once
from the error, then gives the fallback model one try. Both store what the run
wrote under `outputs/` as the user's files and list them on the step.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

import httpx

from app.core.config import settings
from app.services import (
    file_schema,
    governance,
    sandbox,
    settings_store,
    skill_packages,
    thinking,
)
from app.services.tools.base import Tool, ToolContext, ToolResult

#: Schema text handed to the code model, all files together.
_SCHEMA_BUDGET = 9_000
_EXIT_MARK = "__KLOUDCHAT_EXIT__"
#: What the step keeps of a run for 코드 보기 / 출력 보기, in characters.
_TRACE_CODE, _TRACE_STDOUT, _TRACE_STDERR = 6_000, 3_000, 1_500
#: An absolute path of two or more segments outside the chat's /mnt/data.
_FOREIGN_PATH = re.compile(r"(?<![\w.)\]/])/(?!mnt/data(?:/|\b))[A-Za-z_.][\w.\-]*(?:/[\w.\-]+)+")

_RULES = """너는 샌드박스에서 실행될 파이썬 스크립트를 쓴다. 규칙:
- 입력 파일은 현재 디렉터리(/mnt/data)에 아래 '입력 파일'의 이름 그대로 있다. 다른 경로나 파일을 지어내지 마라.
- 결과 파일은 outputs/ 아래에만 쓴다(os.makedirs("outputs", exist_ok=True)). 입력 파일은 덮어쓰지 않는다.
- 결과 파일은 한 번에 10개까지만 돌려받는다. 더 많으면 zip 하나나 시트가 여러 개인 엑셀 하나로 묶는다.
- 파일 이름을 code 로 시작하지 말고, build·dist·node_modules 같은 폴더나 숨김 파일도 쓰지 않는다(돌려받지 못한다).
- Python 3.12, 네트워크 없음, pip 설치 불가. 30초 안에 끝나야 하고 메모리는 1GB까지다. 큰 파일은 필요한 열만
  읽거나(usecols) 나눠 읽는다(chunksize, openpyxl read_only=True).
- 쓸 수 있는 것: pandas, numpy, openpyxl, xlsxwriter, matplotlib(한글은 NanumGothic), python-docx,
  python-pptx, reportlab, pdfplumber, pypdf, PyPDF2, subprocess 로 `pdftotext -layout`. LibreOffice는 쓸 수 없다.
- 엑셀에 수식을 넣으면 계산값이 저장되지 않는다. 값이 필요한 칸에는 파이썬으로 계산한 값을 넣는다.
- 아래 스키마의 미리보기 행은 JSON 배열로 보여 준 것이다. 파일 안에 대괄호나 구분 기호가 있다는 뜻이 아니다.
  "1,234" 같은 글자 숫자, 합계·소계 행, ※ 주석 행, 여러 형식의 날짜가 섞였는지 확인하고 처리한다.
- 요청에 없는 값을 지어내지 않는다. 확인한 값과 행 수를 print 로 보여 준다.
- 마지막 줄에는 요약을 JSON 한 줄로 print 한다. 예: {"outputs": ["outputs/결과.xlsx"], "rows": 10, "notes": "..."}
- 설명 없이 ```python 코드 블록 하나만 출력한다."""

_REPAIR = """앞의 코드가 제대로 끝나지 않았다.

{reason}

앞의 코드:
```python
{code}
```

원인을 고친 전체 코드를 같은 규칙으로 다시 출력하라."""


class CodeModelError(RuntimeError):
    """The code model could not be reached or answered nothing usable."""


# ── code model ──────────────────────────────────────────────────────────


def code_models(strict_local: bool) -> list[str]:
    """The writer then the fallback; a strict-local turn gets only `strict-local/` ids."""
    out: list[str] = []
    for model in (settings.code_model, settings.code_fallback_model):
        model = (model or "").strip()
        if not model:
            continue
        if strict_local:
            if model.startswith("local/"):
                model = "strict-local/" + model.split("/", 1)[1]
            elif not model.startswith("strict-local/"):
                continue
        if model not in out:
            out.append(model)
    return out


def unfenced(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:python|py)?[ \t]{0,8}\n(.*?)```", text, re.S)
    return match.group(1).strip() if match else text


async def write_code(model: str, messages: list[dict[str, str]], api_key: str) -> str:
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(150.0, connect=10.0),
        ) as client:
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": 6000,
                "temperature": 0.2,
                **thinking.switch(model),
            }
            response = await client.post("/v1/chat/completions", json=body)
            if thinking.refused(model, response):
                body.pop("reasoning", None)
                body.update(thinking.switch(model))
                response = await client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CodeModelError(f"코드를 쓰는 모델({model})에 닿지 못했습니다.") from exc
    content = str(((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    code = unfenced(content)
    if not code.strip():
        raise CodeModelError(f"코드를 쓰는 모델({model})이 빈 답을 돌려줬습니다.")
    return code


# ── shared ──────────────────────────────────────────────────────────────


def _names(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:MAX_NAMED]


MAX_NAMED = 20


def _missing(names: list[str], available: list[sandbox.InputFile]) -> ToolResult:
    listed = ", ".join(sorted({f.name for f in available})) or "없음"
    return ToolResult(
        content=f"오류: 이 대화에 {', '.join(names)} 파일이 없습니다. 있는 파일: {listed}",
        failed=True,
    )


def _failure_reason(result: sandbox.RunResult, outputs: list[dict]) -> str | None:
    """Why a run needs another try, or None when it did its job."""
    if result.memory_exceeded:
        return (
            f"{sandbox.MEMORY_LIMIT} 코드의 잘못이 아니라 메모리 한도 때문이다. 필요한 열만 읽거나"
            "(usecols, nrows), 나눠 읽고(chunksize, openpyxl read_only=True) 중간 결과를 바로 쓰도록 고쳐라."
        )
    if result.timed_out:
        return f"{sandbox.TIME_LIMIT} 반복을 줄이고 벡터 연산으로 바꿔라."
    if result.crashed:
        return "오류(stderr 끝부분):\n" + result.stderr.strip()[-1500:]
    if not outputs and not result.stdout.strip():
        return "아무것도 출력하지 않았고 outputs/ 에 파일도 없다. 결과를 print 하고 파일은 outputs/ 에 저장하라."
    return None


def _outputs_detail(outputs: list[dict]) -> str:
    if not outputs:
        return ""
    return f"파일 {len(outputs)}개"


def _head(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… ({len(text) - limit:,}자 생략)"


def _scrub(text: str, secrets: list[str]) -> str:
    """No sandbox or service secrets and no paths outside /mnt/data on the step."""
    for secret in secrets:
        text = text.replace(secret, "[비밀값]")
    text = _FOREIGN_PATH.sub(lambda m: "…/" + m.group(0).rsplit("/", 1)[-1], text)
    return governance.mask(text, scope="tool")[0]


def _trace(
    code: str, stdout: str, stderr: str, ctx: ToolContext, *, attempt: int | None = None
) -> dict[str, Any]:
    """The step's `code`/`stdout`/`stderr` heads; masked again like `detail` on masked turns."""
    secrets = [
        value
        for value in (
            ctx.api_key,
            settings.code_interpreter_api_key,
            settings.litellm_master_key,
            settings.tools_gateway_key,
            settings.jwt_secret,
            settings.secret_key,
        )
        if value and len(value) >= 8
    ]
    trace: dict[str, Any] = {}
    for key, text, limit in (
        ("code", code, _TRACE_CODE),
        ("stdout", stdout, _TRACE_STDOUT),
        ("stderr", stderr, _TRACE_STDERR),
    ):
        if text.strip():
            trace[key] = _head(_scrub(text, secrets), limit)
    if attempt is not None and "code" in trace:
        trace["codeAttempt"] = attempt
    return trace


# ── run_skill_script ────────────────────────────────────────────────────


def _skill_command(path: str, args: dict[str, Any]) -> str:
    # Run through bash: the py sandbox masks /proc, which LibreOffice needs for recalculation.
    payload = shlex.quote(json.dumps(args, ensure_ascii=False))
    return (
        f"python3 {shlex.quote(path)} {payload}; status=$?; echo; echo \"{_EXIT_MARK} $status\""
    )


def _exit_status(stdout: str) -> tuple[str, int | None]:
    lines = stdout.rstrip().splitlines()
    if lines and lines[-1].startswith(_EXIT_MARK):
        try:
            status = int(lines[-1].split()[-1])
        except ValueError:
            status = None
        return "\n".join(lines[:-1]), status
    return stdout, None


def _own(skill: str, ctx: ToolContext | None) -> tuple[str, dict[str, Any]] | None:
    """The person's own skill `skill` names (its key or its name), with scripts."""
    own = getattr(ctx, "own_scripts", None) or {}
    text = skill.strip().strip("「」\"'")
    if text in own:
        return text, own[text]
    return next(((k, v) for k, v in own.items() if v.get("name") == text), None)


async def _run_own(key: str, own: dict[str, Any], args: dict[str, Any],
                   ctx: ToolContext) -> ToolResult:
    """A person's own script, run in the sandbox exactly as a shipped one is: mounted
    read-only, given the chat's files, its outputs stored as the person's files."""
    files: dict[str, str] = dict(own.get("files") or {})
    name = str(own.get("name") or "스킬")
    script = str(args.get("script") or "").strip().removesuffix(".py") or next(iter(files), "")
    if script not in files:
        return ToolResult(content=f"오류: 「{name}」에 {script} 스크립트가 없습니다. "
                                  f"있는 것: {', '.join(files)}", failed=True)
    script_args = args.get("args") or {}
    if isinstance(script_args, str):
        try:
            script_args = json.loads(script_args)
        except ValueError:
            script_args = {"file": script_args}
    if not isinstance(script_args, dict):
        return ToolResult(content="오류: args 는 객체여야 합니다.", failed=True)
    client = await sandbox.client()
    if client is None:
        return ToolResult(content="오류: 코드 실행이 설정되지 않았습니다.", failed=True)
    available = await sandbox.session_files(ctx.user_id, ctx.session_id)
    wanted = _names(script_args.get("file") or script_args.get("files"))
    inputs, missing = sandbox.pick_inputs(available, wanted or None)
    if missing:
        return _missing(missing, available)
    folder = "own-" + re.sub(r"[^a-z0-9]", "", key.removeprefix("skill:").lower())[:24]
    path = f"skills/{folder}/scripts/{script}.py"
    try:
        refs = await sandbox.input_refs(client, ctx.sandbox, inputs)
        own_refs = await sandbox.own_refs(client, ctx.sandbox, folder, files)
        command = _skill_command(path, script_args)
        result = await sandbox.run(client, ctx.sandbox, command, lang="bash",
                                   refs=refs + own_refs)
        outputs, notes = await sandbox.collect_outputs(
            client, result, user_id=ctx.user_id, session_id=ctx.session_id, state=ctx.sandbox)
    except sandbox.SandboxError as exc:
        return ToolResult(content=f"오류: {exc}", failed=True)
    stdout, status = _exit_status(result.stdout)
    summary = sandbox.last_json(stdout)
    failed = (result.crashed or (status not in (None, 0))
              or (summary is not None and summary.get("ok") is False))
    head = (f"스킬 「{name}」의 {script} 스크립트가 실패했습니다." if failed
            else f"스킬 「{name}」의 {script} 스크립트를 실행했습니다.")
    if failed and summary and summary.get("error"):
        head += f" 이유: {summary['error']}"
    body_lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if summary is not None and body_lines:
        body_lines = body_lines[:-1]
    return ToolResult(
        content=sandbox.report(result, outputs, notes, summary=summary,
                               stdout="\n".join(body_lines), head=head),
        detail=(f"「{name}」" + (f" · {_outputs_detail(outputs)}" if outputs else "")),
        failed=failed,
        outputs=[{k: row[k] for k in ("id", "name", "size")} for row in outputs] or None,
        trace=_trace(command, stdout, result.stderr, ctx),
    )


async def run_skill_script(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if (own := _own(str(args.get("skill") or ""), ctx)) is not None:
        return await _run_own(own[0], own[1], args, ctx)
    package = skill_packages.find(str(args.get("skill") or ""))
    if package is None or not package.scripts:
        offered = ", ".join(p.key for p in skill_packages.scripted())
        return ToolResult(content=f"오류: 스크립트가 있는 스킬이 아닙니다. 쓸 수 있는 스킬: {offered}", failed=True)
    script = str(args.get("script") or "").strip() or next(iter(package.scripts))
    path = package.script_path(script)
    if path is None:
        return ToolResult(
            content=f"오류: {package.key} 에 {script} 스크립트가 없습니다. 있는 것: {', '.join(package.scripts)}",
            failed=True,
        )
    script_args = args.get("args") or {}
    if isinstance(script_args, str):
        try:
            script_args = json.loads(script_args)
        except ValueError:
            script_args = {"file": script_args}
    if not isinstance(script_args, dict):
        return ToolResult(content="오류: args 는 객체여야 합니다.", failed=True)
    client = await sandbox.client()
    if client is None:
        return ToolResult(content="오류: 코드 실행이 설정되지 않았습니다.", failed=True)

    available = await sandbox.session_files(ctx.user_id, ctx.session_id)
    wanted = _names(script_args.get("file") or script_args.get("files"))
    inputs, missing = sandbox.pick_inputs(available, wanted or None)
    if missing:
        return _missing(missing, available)
    try:
        refs = await sandbox.input_refs(client, ctx.sandbox, inputs)
        skill_refs = await sandbox.skill_refs(client, package)
        command = _skill_command(path, script_args)
        result = await sandbox.run(client, ctx.sandbox, command, lang="bash", refs=refs + skill_refs)
        if "No such file or directory" in result.stderr and path in result.stderr:
            # The package refs expired on the sandbox: upload again and retry once.
            skill_refs = await sandbox.skill_refs(client, package, fresh=True)
            result = await sandbox.run(client, ctx.sandbox, command, lang="bash", refs=refs + skill_refs)
        outputs, notes = await sandbox.collect_outputs(
            client, result, user_id=ctx.user_id, session_id=ctx.session_id, state=ctx.sandbox
        )
    except sandbox.SandboxError as exc:
        return ToolResult(content=f"오류: {exc}", failed=True)

    stdout, status = _exit_status(result.stdout)
    summary = sandbox.last_json(stdout)
    failed = (
        result.crashed
        or (status not in (None, 0))
        or (summary is not None and summary.get("ok") is False)
    )
    head = f"스킬 「{package.name}」의 {script} 스크립트를 실행했습니다."
    if failed:
        head = f"스킬 「{package.name}」의 {script} 스크립트가 실패했습니다."
        if summary and summary.get("error"):
            head += f" 이유: {summary['error']}"
    body_lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if summary is not None and body_lines:
        body_lines = body_lines[:-1]
    return ToolResult(
        content=sandbox.report(
            result, outputs, notes, summary=summary, stdout="\n".join(body_lines), head=head
        ),
        detail=(f"「{package.name}」" + (f" · {_outputs_detail(outputs)}" if outputs else "")),
        failed=failed,
        outputs=[{k: row[k] for k in ("id", "name", "size")} for row in outputs] or None,
        trace=_trace(command, stdout, result.stderr, ctx),
    )


def _skill_tool_description(own: dict[str, dict[str, Any]] | None = None) -> str:
    lines = []
    for package in skill_packages.scripted():
        scripts = ", ".join(
            f"{name}({summary})" if summary else name for name, summary in package.scripts.items()
        )
        lines.append(f"{package.key} 「{package.name}」: {scripts}")
    for key, row in (own or {}).items():
        lines.append(f"{key} 「{row.get('name')}」(직접 올린 스킬): "
                     + ", ".join(sorted(row.get("files") or {})))
    return (
        "고정된 스킬 스크립트를 샌드박스에서 이 대화의 첨부 파일에 대해 실행합니다. 엑셀·CSV 정리, "
        "PDF 표 추출, 여러 수치 검산처럼 아래 스크립트가 하는 일은 코드를 쓰지 말고 이 도구를 "
        "쓰세요. args 는 스킬 지침에 적힌 대로 넘기고, 입력 파일 이름은 args.file 에 넣습니다. "
        "결과 파일은 사용자에게 내려받기 단추로 붙습니다. 스크립트: " + "; ".join(lines)
    )


# ── run_code ────────────────────────────────────────────────────────────


def _schemas(inputs: list[sandbox.InputFile]) -> str:
    blocks, used = [], 0
    for file in inputs:
        try:
            block = file_schema.describe(file.name, sandbox.read_input(file))
        except OSError:
            block = f"파일 {json.dumps(file.name, ensure_ascii=False)} (읽지 못함)"
        if used + len(block) > _SCHEMA_BUDGET:
            blocks.append(f"파일 {json.dumps(file.name, ensure_ascii=False)} (스키마 생략)")
            continue
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks) if blocks else "(입력 파일 없음)"


async def run_code(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    task = str(args.get("task") or "").strip()
    if not task:
        return ToolResult(content="오류: task 가 비었습니다. 무엇을 할지 한국어로 적으세요.", failed=True)
    models = code_models(ctx.strict_local)
    if not models:
        return ToolResult(content="오류: 코드를 쓸 모델이 설정되지 않았습니다.", failed=True)
    client = await sandbox.client()
    if client is None:
        return ToolResult(content="오류: 코드 실행이 설정되지 않았습니다.", failed=True)
    available = await sandbox.session_files(ctx.user_id, ctx.session_id)
    wanted = _names(args.get("files"))
    inputs, missing = sandbox.pick_inputs(available, wanted or None)
    if missing:
        return _missing(missing, available)

    messages = [
        {"role": "system", "content": _RULES},
        {
            "role": "user",
            "content": f"작업: {task[:4000]}\n\n입력 파일:\n{_schemas(inputs)}",
        },
    ]
    # (model, is a repair) — one repair on the writer, then one try on the fallback.
    plan = [(models[0], False), (models[0], True)] + [(m, True) for m in models[1:2]]
    attempts: list[str] = []
    code = ""
    #: The code behind `result` and which attempt wrote it; a later failed write leaves both.
    ran_code, ran_attempt = "", 0
    result: sandbox.RunResult | None = None
    outputs: list[dict] = []
    notes: list[str] = []
    reason: str | None = None
    try:
        refs = await sandbox.input_refs(client, ctx.sandbox, inputs)
        for model, repair in plan:
            prompt = messages
            if repair:
                prompt = [*messages, {"role": "user", "content": _REPAIR.format(reason=reason, code=code)}]
            try:
                code = await write_code(model, prompt, ctx.api_key)
            except CodeModelError as exc:
                attempts.append(f"{model}: {exc}")
                reason = reason or str(exc)
                continue
            result = await sandbox.run(client, ctx.sandbox, code, lang="py", refs=refs)
            ran_code, ran_attempt = code, len(attempts) + 1
            stored: list[dict] = []
            if not result.crashed:
                # A crashed attempt's half-written files are not handed to the user.
                stored, more = await sandbox.collect_outputs(
                    client, result, user_id=ctx.user_id, session_id=ctx.session_id,
                    state=ctx.sandbox,
                )
                notes += more
            outputs += stored
            reason = _failure_reason(result, stored)
            attempts.append(f"{model}: {'성공' if reason is None else '실패'}")
            if reason is None:
                break
    except sandbox.SandboxError as exc:
        return ToolResult(content=f"오류: {exc}", failed=True)

    if result is None:
        return ToolResult(content="오류: " + "; ".join(attempts), failed=True)
    summary = sandbox.last_json(result.stdout)
    head = (
        f"작업을 마쳤습니다 (시도: {' → '.join(attempts)})."
        if reason is None
        else f"작업을 끝내지 못했습니다 (시도: {' → '.join(attempts)}). 마지막 실패 이유: {reason[:600]}"
    )
    content = sandbox.report(result, outputs, notes, summary=summary, head=head)
    content += f"\n\n마지막으로 실행한 코드:\n```python\n{code[:3000]}\n```"
    return ToolResult(
        content=content,
        detail=" · ".join(filter(None, [f"시도 {len(attempts)}회", _outputs_detail(outputs)])),
        failed=reason is not None,
        outputs=[{k: row[k] for k in ("id", "name", "size")} for row in outputs] or None,
        trace=_trace(ran_code, result.stdout, result.stderr, ctx, attempt=ran_attempt),
    )


# ── execute_code ────────────────────────────────────────────────────────


async def execute_code(args: dict[str, Any], ctx: ToolContext | None = None) -> ToolResult:
    """The model's own code, with the chat files it names mounted and `outputs/` kept."""
    code = str(args.get("code") or "")
    if not code.strip():
        return ToolResult(content="오류: code 가 비었습니다.", failed=True)
    client = await sandbox.client()
    if client is None:
        return ToolResult(content="오류: 코드 실행이 설정되지 않았습니다.", failed=True)
    state: dict[str, Any] = ctx.sandbox if ctx is not None else {}
    try:
        refs: list[dict] = []
        if ctx is not None:
            available = await sandbox.session_files(ctx.user_id, ctx.session_id)
            named = [f for f in available if f.name in code]
            inputs, _ = sandbox.pick_inputs(named, None)
            refs = await sandbox.input_refs(client, state, inputs)
        result = await sandbox.run(client, state, code, lang="py", refs=refs)
        outputs, notes = [], []
        if ctx is not None:
            outputs, notes = await sandbox.collect_outputs(
                client, result, user_id=ctx.user_id, session_id=ctx.session_id, state=state
            )
    except sandbox.SandboxError as exc:
        return ToolResult(content=f"오류: {exc}", failed=True)
    if not result.stdout.strip() and not result.stderr.strip() and not outputs:
        content = "실행되었지만 출력이 없습니다. 결과를 보려면 print() 를 쓰세요."
    else:
        content = sandbox.report(result, outputs, notes)
    return ToolResult(
        content=content,
        failed=bool(result.crashed or (result.stderr.strip() and not result.stdout.strip())),
        detail=_outputs_detail(outputs) or None,
        outputs=[{k: row[k] for k in ("id", "name", "size")} for row in outputs] or None,
    )


# ── tools ───────────────────────────────────────────────────────────────

EXECUTE_CODE = Tool(
    name="execute_code",
    description=(
        "샌드박스에서 Python 코드를 실행합니다. 계산, 수식 전개(sympy), 데이터 처리에 "
        "쓰세요. 결과는 반드시 print() 로 출력해야 보입니다. 네트워크는 막혀 있습니다. "
        "코드에 이름이 나온 이 대화의 첨부 파일은 현재 디렉터리에 놓이고, outputs/ 에 쓴 "
        "파일은 사용자에게 돌아갑니다. 첨부 파일을 다루는 일은 run_skill_script 나 run_code 가 낫습니다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "실행할 코드. 출력은 print() 로."},
            "language": {"type": "string", "enum": ["python"], "default": "python"},
        },
        "required": ["code"],
    },
    run=execute_code,
    label="코드 실행 중",
    title="코드 실행",
    wants_context=True,
)

RUN_CODE = Tool(
    name="run_code",
    description=(
        "이 대화에 첨부된 파일(엑셀·CSV·PDF·워드·파워포인트)로 할 일을 한국어로 적어 주면, "
        "코드 모델이 파이썬을 써서 샌드박스에서 실행하고 결과 파일을 사용자에게 돌려줍니다. "
        "피벗·집계, 시트 병합, 열 나누기, 조건 표시, 단위 환산, PDF 쪽 추출·병합·회전, 차트 그림 "
        "같은 작업에 쓰세요. 엑셀 정리·PDF 표 추출처럼 스킬 스크립트가 있는 일은 run_skill_script 를 "
        "먼저 쓰세요. 직접 코드를 쓰지 말고 무엇을 어떤 파일로 만들지 task 에 분명히 적으세요."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "할 일. 입력 파일, 원하는 결과 파일 이름과 형식, 시트·열 이름, 조건을 "
                    "사용자의 말 그대로 구체적으로. 예: '예산.xlsx 의 금액을 원 단위로 바꾸고 "
                    "월별 합계 시트를 더해 예산_원.xlsx 로'"
                ),
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "쓸 입력 파일 이름. 생략하면 이 대화의 파일 전부.",
            },
        },
        "required": ["task"],
    },
    run=run_code,
    label="파일 작업 중",
    title="파일 작업",
    read_only=False,
    wants_context=True,
)


def run_skill_script_tool(own: dict[str, dict[str, Any]] | None = None) -> Tool:
    keys = [package.key for package in skill_packages.scripted()] + sorted(own or {})
    return Tool(
        name="run_skill_script",
        description=_skill_tool_description(own),
        parameters={
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": keys, "description": "스킬 키"},
                "script": {"type": "string", "description": "스크립트 이름(.py 없이)"},
                "args": {
                    "type": "object",
                    "description": "스크립트 인자. 입력 파일은 file 에 첨부 파일 이름 그대로.",
                },
            },
            "required": ["skill", "script"],
        },
        run=run_skill_script,
        label="스킬 스크립트 실행 중",
        title="스킬 스크립트",
        read_only=False,
        wants_context=True,
    )


RUN_SKILL_SCRIPT = run_skill_script_tool()


def with_own_scripts(tools: list[Tool], own: dict[str, dict[str, Any]]) -> list[Tool]:
    """The turn's tools with `run_skill_script` offering the person's own scripts too; a
    turn without the tool (not allowed, no sandbox) gains nothing."""
    if not own:
        return tools
    return [run_skill_script_tool(own) if tool.name == RUN_SKILL_SCRIPT.name else tool
            for tool in tools]

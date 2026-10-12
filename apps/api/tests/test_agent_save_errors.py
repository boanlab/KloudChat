"""Every refusal of an agent save reads as a Korean sentence on the form, never a code.

No 「저장하지 못했습니다. (gap_keywords_empty)」 on screen: the form
maps each code to a sentence and a place (`apps/web/src/lib/agentErrors.ts`); this holds every
code the agent save, its 빠진 정보 질문, its 세부 설정 and its 진행 과정 can raise to that map.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.routers import user_pipelines, workspace
from app.services import gap_questions, process_settings

_MAP = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "agentErrors.ts"

#: What `POST/PATCH /agents` runs before saving, and `PUT /agents/{id}/process`.
_SAVE = (workspace._claim_slug, workspace._own, workspace._validate_skill_ids,
         workspace._validate_tool_names, workspace._validated_checks,
         workspace._validate_design_system_id, workspace._validate_agent_bindings,
         workspace.create_agent, workspace.patch_agent, user_pipelines.put_process,
         user_pipelines._invalid)


def _raised() -> set[str]:
    codes: set[str] = set()
    for fn in _SAVE:
        source = inspect.getsource(fn)
        codes |= set(re.findall(r'detail=f?"([a-z][a-z0-9_]*)', source))
        codes |= set(re.findall(r'_process_refused\(\s*"([a-z][a-z0-9_]*)"', source))
        codes |= set(re.findall(r'"code":\s*"([a-z][a-z0-9_]*)"', source))
    for module, error in ((gap_questions, "QuestionError"), (process_settings, "ProcessError")):
        source = inspect.getsource(module)
        codes |= set(re.findall(error + r'\(\s*"([a-z][a-z0-9_]*)"', source))
    # 세부 설정's list and number fields name their code as an argument.
    codes |= set(re.findall(r'"(process_[a-z_]+)"', inspect.getsource(process_settings)))
    return codes


def test_the_audit_finds_the_codes_people_met():
    codes = _raised()
    assert {"gap_keywords_empty", "slug_taken", "unknown_tools", "process_outline",
            "base_required", "invalid_pipeline", "design_template_surface_mismatch"} <= codes


def test_every_code_an_agent_save_raises_has_a_korean_sentence_on_the_form():
    if not _MAP.is_file():
        pytest.skip("the web app is not beside the API here")
    text = _MAP.read_text(encoding="utf-8")
    body = text[text.index("AGENT_SAVE_ERRORS"):text.index("const CODE")]
    entries = dict(re.findall(r"^\s{2}([a-z][a-z0-9_]*): \{\s*message: '([^']+)'", body, re.M))
    missing = sorted(_raised() - set(entries))
    assert not missing, f"add these to apps/web/src/lib/agentErrors.ts: {missing}"
    assert all(re.search(r"[가-힣]", m) and not re.search(r"[a-z]+_[a-z]+", m)
               for m in entries.values())

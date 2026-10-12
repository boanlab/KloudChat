"""Test-local text skills; the shipped catalogue holds packages only."""

from __future__ import annotations

from app.services import starter

CITATION_V1 = "형식을 먼저 확인한다."

TEXT_SKILLS = [
    {
        "key": "citation",
        "name": "인용 형식 맞추기",
        "description": "참고문헌과 본문 인용을 한 형식으로 맞춥니다.",
        "when_to_use": "인용 형식이 정해진 글을 쓸 때.",
        "kinds": ["chat", "report", "slides"],
        "version": "1.1.0",
        "body": "형식을 먼저 확인한다. 제공된 검색 결과에 없는 문헌은 만들지 않는다.",
    },
    {
        "key": "evidence",
        "name": "수치에 근거 붙이기",
        "description": "숫자마다 출처를 답니다.",
        "when_to_use": "수치가 판단 근거가 되는 글을 쓸 때.",
        "kinds": ["chat", "report", "slides"],
        "version": "1.1.0",
        "body": "모든 숫자에 출처를 적는다.",
    },
    {
        "key": "quiz-writer",
        "name": "문제 출제",
        "description": "문제를 먼저 내고 정답은 마지막에 모읍니다.",
        "when_to_use": "이해를 확인하는 문제를 만들 때.",
        "kinds": ["chat", "slides"],
        "version": "1.0.0",
        "body": "문제를 전부 먼저, 정답과 해설은 마지막에.",
    },
]


def use_text_skills(monkeypatch) -> None:
    specs = TEXT_SKILLS + list(starter._SKILLS)
    monkeypatch.setattr(starter, "_SKILLS", specs)
    keys = {starter._slug(spec["name"]): spec["key"] for spec in specs}
    monkeypatch.setattr(starter, "_LEGACY_CATALOG_KEYS", keys)
    monkeypatch.setattr(
        starter,
        "_LEGACY_CATALOG_BODIES",
        {**starter._LEGACY_CATALOG_BODIES, ("citation", "1.0.0"): CITATION_V1},
    )

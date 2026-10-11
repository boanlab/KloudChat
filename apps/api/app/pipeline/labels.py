"""Korean stage names shown on a run's record."""

from __future__ import annotations

LABELS: dict[str, str] = {
    "check:gaps": "빠진 정보 찾기",
    "gap-card": "확인 질문",
    "research": "자료 검색",
    "material": "자료 모으기",
    "outline": "개요 잡기",
    "approved-plan": "승인한 개요",
    "calc": "값 계산",
    "announce": "작성 시작",
    "settle": "핵심 수치 정하기",
    "prepass": "사전 계산",
    "writer-notes": "작성 지침",
    "draft": "초안",
    "sections": "섹션 쓰기",
    "figures": "그림",
    "additions": "보탤 내용",
    "normalize": "다듬기",
    "title-block": "머리 정보",
    "checks-plan": "검사 계획",
    "minutes-repair": "회의록 행 고치기",
    "check:grounding": "근거 확인",
    "format-find": "템플릿 검사",
    "verify": "검증",
    "minutes-reground": "회의록 다시 맞추기",
    "check:grounding-again": "근거 다시 확인",
    "format-amend": "템플릿대로 고치기",
    "career-periods-tidy": "경력 기간 정리",
    "page-limit": "분량 맞추기",
    "format-step": "템플릿 점검 결과",
    "quality": "품질 점검",
    "report-save": "저장",
    "answer-normalize": "답 다듬기",
    "check:answer-grounding": "근거 확인",
    "check:copy": "카피 점검",
    "check:skills": "스킬 점검",
    "text-fit": "분량 확인",
    "answer-replace": "답 바꾸기",
    "revise-route": "고칠 곳 찾기",
    "revise-widen": "함께 고칠 곳",
    "revise-rewrite": "고쳐 쓰기",
    "consistency": "앞뒤 맞추기",
    "revise-load": "문서 불러오기",
    "revise-save": "저장",
    "export": "파일 만들기",
    "deck-write": "슬라이드 쓰기",
    "deck-carry": "그대로 둘 슬라이드",
    "deck-save": "저장",
    "page-write": "페이지 쓰기",
    "page-save": "저장",
    "route": "요청 살피기",
}


def label(name: str) -> str:
    return LABELS.get(name, name)


__all__ = ["LABELS", "label"]

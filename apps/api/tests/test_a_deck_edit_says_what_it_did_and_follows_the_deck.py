"""A slide edit says what it changed, a structure figure holds the parts the deck names, an
English deck speaks English, and a stated subject is a subject."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.pipeline import check_stages, revise_stages, save_stages
from app.pipeline.stage import Context, Stop
from app.services import chat as chat_service
from app.services import check_skills, deck, grounding

EXEC = (
    "사업부장이 임원회의에 보고할 슬라이드 8장 안팎으로 만들어줘. 주제: 2026년 3분기 사업 실적과 "
    "4분기 계획. 수치: 매출 128억(전년 동기 114억, +12%), 영업이익 9.6억(이익률 7.5%, 전분기 "
    "6.8%), 지역별 매출 수도권 71억·영남 33억·호남 14억·기타 10억, 신규 거래처 3곳, 출고 지연 "
    "4일(영남 물류센터 이전 탓). 4분기 목표 매출 140억, 출고 지연 0일. 요청 사항: 영남 물류 "
    "안정화 예산 1.5억 승인."
)
TALK = (
    "Create a 5-slide English presentation for a conference talk: 'Can small on-premise "
    "language models replace cloud APIs for document drafting?' Findings: a 27B local model "
    "scored 82% of a frontier cloud model on a 40-task drafting benchmark (rated by 3 "
    "reviewers), cost per 1,000 documents was $4 vs $38, latency 9s vs 14s, but failed more on "
    "long-context tasks (64% vs 91% above 20 pages). Recommendation: hybrid routing."
)
FLOW = {"key": "9e1a19752abba613", "figure": "flow",
        "source": 'flowchart LR\n  s0("예산 투입") --> s1("지연 해소")'}


def _exec_slides() -> list[dict]:
    slides = [{"id": f"s{i}", "layout": "bullets", "title": f"{i + 1}장", "bullets": ["가"]}
              for i in range(8)]
    # the only picture is the deck's own flow diagram (its rendering and its source)
    slides[6] = {**slides[6], "title": "예산 승인 요청",
                 "image": {"src": "data:image/png;base64,AA", "fit": "contain",
                           "key": FLOW["key"]},
                 "diagram": dict(FLOW)}
    return slides


def _bundle(typed: str, slides: list[dict]):
    return revise_stages.bundle_for(
        request=EXEC, instruction=typed, model={"id": "m"}, planner=None, api_key="k",
        is_deck=True, title="보고", data={}, parts=slides, material=[], format_id="",
        plan=None, usage={"inputTokens": 0, "outputTokens": 0})


async def _no_planner(**kwargs):
    raise AssertionError("the planner is not asked where the pictures are")


@pytest.mark.asyncio
async def test_taking_the_picture_away_removes_a_drawn_diagram_and_says_which(monkeypatch):
    monkeypatch.setattr(revise_stages.revise, "plan", _no_planner)
    bundle = _bundle("그림 빼줘.", _exec_slides())
    _ = [e async for e in revise_stages.Route().run(bundle, Context())]
    assert bundle.plan.revision.targets == [6]
    bundle.plan.order = [6]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    slide = bundle.document.sections[6]
    assert "image" not in slide and "diagram" not in slide and slide["bullets"] == ["가"]
    assert bundle.document.removed == ["7장의 흐름도"] and bundle.document.changed == 1
    assert bundle.document.unmet == []
    assert save_stages.removed_said(bundle.document.removed) == "7장의 흐름도를 뺐습니다."
    assert save_stages.removed_said(["3장의 그림"]) == "3장의 그림을 뺐습니다."


@pytest.mark.asyncio
async def test_a_deck_with_no_picture_is_told_so_not_told_it_was_changed(monkeypatch):
    monkeypatch.setattr(revise_stages.revise, "plan", _no_planner)
    plain = [{k: v for k, v in s.items() if k not in ("image", "diagram")}
             for s in _exec_slides()]
    for typed, said in (("그림 빼줘.", "슬라이드에 뺄 그림이 없어"),
                        ("3장 그림은 지워 줘", "3장에는 뺄 그림이 없어")):
        bundle = _bundle(typed, [dict(s) for s in plain])
        events = []
        with pytest.raises(Stop):
            async for event in revise_stages.Route().run(bundle, Context()):
                events.append(event)
        text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
        assert text.startswith(said) and "고쳤" not in text
    # a named slide with a picture: that one only
    bundle = _bundle("7장 그림은 빼줘", _exec_slides())
    _ = [e async for e in revise_stages.Route().run(bundle, Context())]
    assert bundle.plan.revision.targets == [6]


def test_a_subject_given_mid_sentence_is_a_subject_and_titles_the_chat():
    planner = json.dumps({"subject": "", "slides": []})
    assert not grounding.subject_missing(planner, EXEC)
    assert chat_service.provisional_title(EXEC) == "2026년 3분기 사업 실적과 4분기 계획"
    lecture = ("신입 개발자 대상 1시간 강의용 슬라이드를 만들어줘. 주제는 'Kubernetes 구조와 동작 "
               "원리'. 컨트롤 플레인/워커 노드 구조를 설명해줘.")
    assert chat_service.provisional_title(lecture) == "Kubernetes 구조와 동작 원리"
    # no subject named: the prompt's start, as before
    assert chat_service.provisional_title("학과 홍보 영상 4초") == "학과 홍보 영상 4초"
    assert grounding.subject_missing(json.dumps({"subject": ""}), "발표 슬라이드 만들어줘")


TALK_SLIDES = [
    {"layout": "title", "title": "On-Prem LLMs vs Cloud APIs for Document Drafting"},
    {"layout": "bullets", "title": "Benchmark setup and scoring",
     "bullets": ["40 task drafting benchmark", "Rated by 3 independent reviewers"],
     "notes": "This size makes it feasible to deploy on a single workstation."},
    {"layout": "table", "title": "On-prem vs cloud: cost, latency, quality",
     "rows": [["Criterion", "On-prem", "Cloud"], ["Quality score", "82%", "91%"],
              ["Cost / 1,000 docs", "$4", "$38"], ["Latency per doc", "9s", "14s"],
              ["Long-context pass", "64%", "91%"]],
     "notes": "Across these dimensions, the trade-offs are visible."},
    {"layout": "bullets", "title": "Where the 27B model falls short",
     "bullets": ["Long documents above 20 pages"], "notes": "The weakness is long documents."},
    {"layout": "closing", "title": "Hybrid routing"},
]


def test_an_english_deck_s_filled_notes_and_notes_prompt_are_in_english():
    slides = json.loads(json.dumps(TALK_SLIDES))
    filled = deck.fill_missing_notes(slides, TALK, title=slides[0]["title"])
    assert filled == [0, 4]
    assert slides[0]["notes"].startswith("Hello, everyone. Today I will talk about On-Prem")
    assert slides[4]["notes"].startswith("That concludes my talk.")
    assert not any("안녕하세요" in s["notes"] or "마치겠습니다" in s["notes"] for s in slides)
    asked = deck._NOTES_PROMPT.format(heading="h", shown="s", neighbours="", request="r",
                                      language="English")
    assert "in English, the language of the slides" in asked


def test_a_table_value_the_material_gives_for_another_row_goes_back_and_is_marked():
    assert check_stages._given_figures(TALK) >= check_stages._GIVEN_DATA
    table = TALK_SLIDES[2]
    said = check_stages.misplaced_cells(table, TALK)
    assert said == ["「91%」 in row 「Quality score」 is the material's figure for another item; "
                    "put there only what the material gives for that row"]
    options = {**check_skills.options_for(dt.date(2026, 10, 19), surface="deck"),
               "person": TALK, "requirements": True}
    assert check_stages.trace_slides(json.loads(json.dumps(TALK_SLIDES)), material=TALK,
                                     computed=[], options=options) == {2: said}
    marked, count = check_stages.mark_misplaced(json.loads(json.dumps(TALK_SLIDES)), TALK)
    assert count == 1 and marked[2]["rows"][1] == ["Quality score", "82%", "91% (확인 필요)"]
    assert marked[2]["rows"][4] == ["Long-context pass", "64%", "91%"]
    # a value the material gives twice may stand in two rows
    twice = TALK + " Cloud pass rate was 91%."
    assert check_stages.misplaced_cells(table, twice) == []


K8S = [
    {"id": "a", "layout": "title", "title": "Kubernetes 구조와 동작 원리"},
    {"id": "b", "layout": "cards", "title": "클러스터의 두 축: 컨트롤 플레인과 워커 노드",
     "cards": [["컨트롤 플레인", "클러스터의 뇌. API 요청을 받아 스케줄러와 컨트롤러가 노드에 "
                "작업을 배분하는 중앙 관리 영역입니다."],
               ["워커 노드", "실제 애플리케이션 컨테이너가 실행되는 서버. kubelet과 컨테이너 "
                "런타임이 Pod 상태를 유지합니다."]]},
    {"id": "c", "layout": "bullets", "title": "컨트롤 플레인 핵심 컴포넌트",
     "bullets": ["kube-apiserver: 모든 API 요청 처리", "etcd: 클러스터 데이터 저장소",
                 "kube-scheduler: 노드 할당 결정"]},
    {"id": "d", "layout": "bands", "title": "워커 노드의 역할과 구성",
     "bands": [["kubelet", "Pod 내 컨테이너 실행 보장"], ["kube-proxy", "서비스 네트워크 라우팅"],
               ["Container Runtime", "이미지를 가져와 실행"]]},
    {"id": "e", "layout": "two-column", "title": "에이전트 vs 데몬셋: 컨트롤 플레인과 워커 노드",
     "bullets": ["DaemonSet: 노드마다 하나"]},
]


@pytest.mark.asyncio
async def test_a_structure_figure_asked_on_a_slide_holds_the_parts_the_deck_names(monkeypatch):
    assert deck.group_members(K8S, K8S[1]) == {
        "컨트롤 플레인": ["kube-apiserver", "etcd", "kube-scheduler"],
        "워커 노드": ["kubelet", "kube-proxy", "Container Runtime"],
    }
    drawn: list[str] = []

    async def make(planned, *, model, api_key, slide, accent=""):
        drawn.append(planned.description)
        # the first drawing leaves out etcd and kube-proxy
        nodes = (["kube-apiserver", "kube-scheduler", "kubelet", "Container Runtime"]
                 if len(drawn) == 1 else
                 ["kube-apiserver", "etcd", "kube-scheduler", "kubelet", "kube-proxy",
                  "Container Runtime"])
        source = "flowchart LR\n" + "\n".join(f"  n{i}[{n}]" for i, n in enumerate(nodes))
        return ({"figure": planned.figure, "description": planned.description,
                 "source": source, "caption": planned.caption, "key": "k" * 16},
                {"inputTokens": 1, "outputTokens": 1}, b"\x89PNG")

    monkeypatch.setattr(deck.diagrams, "make", make)

    written, usage = await deck.figure_slide(
        request="Kubernetes 강의", slides=K8S, target_id="b", instruction="3장을 구조도로 바꿔줘",
        model="m", api_key="k")
    assert "etcd" in drawn[0] and "kube-proxy" in drawn[0]
    assert "left out etcd, kube-proxy" in drawn[1]
    assert "etcd" in written["diagram"]["source"] and "kube-proxy" in written["diagram"]["source"]
    assert usage == {"inputTokens": 2, "outputTokens": 2}
    # two cards under the figure keep their words whole (they wrap), no 「…」
    texts = [text for _, text in written["cards"]]
    assert texts == [card[1] for card in K8S[1]["cards"]]
    assert not any(text.endswith("…") for text in texts)


def test_words_under_a_figure_are_cut_at_a_sentence_not_mid_word():
    long = ("클러스터의 상태를 저장하는 중앙 저장소입니다. 모든 오브젝트의 스펙과 상태가 "
            "키-값 형태로 보관되며 장애 시 복구의 기준이 됩니다.")
    cut = deck._fitted(long, 44)
    assert cut == "클러스터의 상태를 저장하는 중앙 저장소입니다."
    assert deck._fitted("짧은 글", 44) == "짧은 글"
    assert deck._fitted("가" * 80, 44).endswith("…")

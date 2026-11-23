"""구조도·흐름도·개념도 on an outside picture model: labels from the request, one checked redraw."""

from __future__ import annotations

import json

import pytest

from app.services import imagegen, paper_figure

REQUEST = (
    "데이터 수집, 전처리, 모델 학습, 평가 순서의 구조도. 학습 결과는 평가에서 수집으로 되돌린다"
)

PLAN_JSON = json.dumps(
    {
        "regions": [
            {
                "label": "",
                "nodes": [
                    {"label": "데이터 수집", "kind": "data"},
                    {"label": "전처리", "kind": "process"},
                    {"label": "모델 학습", "kind": "process", "emphasis": True},
                    {"label": "평가", "kind": "loss"},
                    {"label": "취수", "kind": "process"},
                ],
            }
        ],
        "edges": [
            {"from": "데이터 수집", "to": "전처리", "label": "", "style": "main"},
            {"from": "전처리", "to": "모델 학습", "label": "", "style": "main"},
            {"from": "모델 학습", "to": "평가", "label": "", "style": "main"},
            {"from": "평가", "to": "데이터 수집", "label": "", "style": "feedback"},
            {"from": "평가", "to": "취수", "label": "", "style": "main"},
        ],
    },
    ensure_ascii=False,
)


def _image(tag: int = 0) -> imagegen.GeneratedImage:
    return imagegen.GeneratedImage(
        data=b"x%d" % tag, mime="image/png", input_tokens=1, output_tokens=1
    )


def test_a_label_the_request_does_not_contain_is_dropped_from_the_plan() -> None:
    plan = paper_figure.parse_plan(PLAN_JSON, REQUEST)
    assert plan is not None
    assert plan.labels == ["데이터 수집", "전처리", "모델 학습", "평가"]
    assert all(edge.target != "취수" for edge in plan.edges)
    assert [n.emphasis for n in plan.regions[0].nodes] == [False, False, True, False]


def test_a_plan_with_less_than_two_nodes_is_refused() -> None:
    assert paper_figure.parse_plan('{"regions": [{"nodes": [{"label": "평가"}]}]}', REQUEST) is None
    assert paper_figure.parse_plan("not json", REQUEST) is None


def test_an_english_request_accepts_english_labels() -> None:
    text = json.dumps({"regions": [{"nodes": [{"label": "Encoder"}, {"label": "Decoder"}]}]})
    plan = paper_figure.parse_plan(text, "An encoder feeds a decoder")
    assert plan is not None and plan.labels == ["Encoder", "Decoder"]


def test_the_prompt_lists_every_label_apart_from_the_instructions() -> None:
    plan = paper_figure.parse_plan(PLAN_JSON, REQUEST)
    prompt = paper_figure.build_prompt(plan, style="구조도", aspect="16:9")
    head, labels = prompt.split("LABEL LIST (the only text allowed in the picture):\n")
    labels = labels.split("\n\n")[0].splitlines()
    assert labels == ['1. "데이터 수집"', '2. "전처리"', '3. "모델 학습"', '4. "평가"']
    assert "STRUCTURE (instructions for you, not text to draw)" in head
    assert "Write no other word" in head and "16:9" in prompt


def test_a_missing_misspelled_and_stray_label_are_told_apart() -> None:
    expected = ["데이터 수집", "전처리", "모델 학습", "평가"]
    check = paper_figure.compare(expected, ["데이터 수집", "전 처리", "모델 학슴", "Figure 1"])
    assert check.missing == ["모델 학습", "평가"]
    assert check.extra == ["모델 학슴", "Figure 1"]
    assert check.misspelled == [("모델 학습", "모델 학슴")]
    assert check.score == 4
    assert paper_figure.compare(expected, ["데이터", "수집", "전처리", "모델 학습", "평가"]).clean


def test_case_spaces_and_punctuation_do_not_count() -> None:
    assert paper_figure.compare(["Loss (L2)"], ["loss  l2"]).clean


def test_only_a_figure_chip_on_an_outside_drawing_model_goes_this_way() -> None:
    outside = {
        "id": "google/gemini-nano-banana-2.1",
        "kinds": ["image"],
        "dataBoundary": "external",
    }
    inside = {
        "id": "strict-local/flux2-klein-4b",
        "kinds": ["image"],
        "dataBoundary": "self_hosted",
        "strictLocal": True,
    }
    assert paper_figure.routes_here("구조도", outside)
    assert paper_figure.routes_here("개념도", outside)
    assert not paper_figure.routes_here("차트", outside)
    assert not paper_figure.routes_here("사진", outside)
    assert not paper_figure.routes_here("구조도", inside)
    assert not paper_figure.routes_here("구조도", None)


@pytest.fixture
def scripted(monkeypatch):
    """imagegen.generate hands out pictures in order; read_text answers from a script."""
    state = {"prompts": [], "reads": [], "present": set()}

    async def generate(**kwargs):
        state["prompts"].append(kwargs["prompt"])
        return _image(len(state["prompts"]))

    async def read_text(image, *, model, api_key):
        return state["reads"].pop(0) if state["reads"] else None

    async def confirm(image, labels, *, model, api_key):
        state.setdefault("confirmers", []).append(model)
        return {label for label in labels if label in state["present"]}

    monkeypatch.setattr(paper_figure, "confirm", confirm)
    monkeypatch.setattr(imagegen, "generate", generate)
    monkeypatch.setattr(paper_figure, "read_text", read_text)
    return state


async def _draw(reader, second=None):
    plan = paper_figure.parse_plan(PLAN_JSON, REQUEST)
    return await paper_figure.draw(
        plan,
        style="구조도",
        aspect="16:9",
        base_url="http://llm",
        api_key="k",
        model="google/gemini-nano-banana-2.1",
        reader=reader,
        second_reader=second,
    )


@pytest.mark.asyncio
async def test_a_picture_that_reads_right_is_drawn_once(scripted) -> None:
    scripted["reads"] = [["데이터 수집", "전처리", "모델 학습", "평가"]]
    done = await _draw("vision/model")
    assert (done.checked, len(done.drawn), done.issues) == ("ok", 1, 0)


@pytest.mark.asyncio
async def test_a_picture_with_a_wrong_label_is_redrawn_once_and_the_better_one_kept(
    scripted,
) -> None:
    scripted["reads"] = [
        ["데이터 수집", "전처리", "모델 학슴", "평가"],
        ["데이터 수집", "전처리", "모델 학습", "평가"],
    ]
    done = await _draw("vision/model")
    assert (done.checked, len(done.drawn), done.issues) == ("retried", 2, 0)
    assert done.image.data == b"x2"
    assert 'was drawn as "모델 학슴"' in scripted["prompts"][1]
    assert "CORRECTIONS" not in scripted["prompts"][0]


@pytest.mark.asyncio
async def test_a_redraw_that_is_worse_loses_to_the_first_and_there_is_no_third(scripted) -> None:
    scripted["reads"] = [["데이터 수집", "전처리", "모델 학슴", "평가"], ["평가"]]
    done = await _draw("vision/model")
    assert done.image.data == b"x1" and len(done.drawn) == 2 and done.checked == "retried"
    assert len(scripted["prompts"]) == 2


@pytest.mark.asyncio
async def test_without_a_model_that_reads_pictures_the_check_is_skipped(scripted) -> None:
    done = await _draw(None)
    assert (done.checked, len(done.drawn)) == ("skipped", 1)


@pytest.mark.asyncio
async def test_a_reader_that_cannot_answer_skips_the_check(scripted) -> None:
    done = await _draw("vision/model")
    assert (done.checked, len(done.drawn)) == ("skipped", 1)


@pytest.mark.asyncio
async def test_a_typo_the_reader_invented_is_cleared_by_the_second_look(scripted) -> None:
    scripted["reads"] = [["데이터 수집", "전처리", "모델 학슴", "평가"]]
    scripted["present"] = {"모델 학습"}
    done = await _draw("vision/model")
    assert (done.checked, len(done.drawn), done.issues) == ("ok", 1, 0)


@pytest.mark.asyncio
async def test_a_second_model_gives_the_second_look_at_what_the_first_reading_found_wrong(
    scripted,
) -> None:
    scripted["reads"] = [["데이터 수집", "전처리", "모델 학슴", "평가"]]
    scripted["present"] = {"모델 학습"}
    await _draw("vision/first", "vision/second")
    assert scripted["confirmers"] == ["vision/second"]

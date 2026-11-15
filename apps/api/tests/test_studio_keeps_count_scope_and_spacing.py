"""지식 서가 studio: a quiz short of its count is topped up, an answer cut off for room is
asked again with more room, a numbered range in the request (「1~7강」) narrows the sources,
and sources that lost their word spacing do not pull the checks' repairs toward glued text."""

from __future__ import annotations

import json

from test_knowledge_shelf import shelf  # noqa: F401 — the fixture

from app.models.workspace import StoredFile
from app.services import shelf_calls, shelf_checks, shelf_jobs, studio_kinds


def _mc(question: str, right: str, explanation: str) -> dict:
    return {"type": "mc", "question": question, "choices": [right, "물리 시각", "네트워크 지연",
                                                            "메모리 크기"],
            "answer": 0, "explanation": explanation, "cite": [1]}


_FIRST = [
    _mc("벡터 시계로 판단할 수 있는 것은?", "동시성",
        "벡터 시계로는 두 사건이 동시에 일어났는지 판단할 수 있다 [1]."),
    _mc("벡터 시계의 칸 수는?", "프로세스 수", "벡터 시계는 프로세스 수만큼의 칸을 가진다 [1]."),
    {"type": "short", "question": "같은 프로세스 뒤 사건의 시계 값은?", "answer": "더 큰 값",
     "explanation": "같은 프로세스에서 뒤의 사건은 더 큰 값을 받는다 [1].", "cite": [1]},
]
_MORE = [
    # a repeat of one already kept is not counted
    _FIRST[0],
    {"type": "short", "question": "분산 시스템에 없는 시계는?", "answer": "전역 시계",
     "explanation": "분산 시스템에는 전역 시계가 없다 [1].", "cite": [1]},
    {"type": "short", "question": "램포트 시계가 사건마다 붙이는 것은?", "answer": "정수",
     "explanation": "램포트 시계는 사건마다 정수를 붙여 순서를 정한다 [1].", "cite": [1]},
    {"type": "short", "question": "램포트 시계 값의 대소로 알 수 없는 것은?", "answer": "인과 관계",
     "explanation": "램포트 시계 값의 대소만으로는 인과 관계를 알 수 없다 [1].", "cite": [1]},
]


async def test_a_quiz_short_of_its_count_is_topped_up_once_and_trimmed(shelf):  # noqa: F811
    shelf.answers.append(json.dumps({"title": "퀴즈", "questions": _FIRST}, ensure_ascii=False))
    shelf.answers.append(json.dumps({"title": "퀴즈", "questions": _MORE}, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "quiz", "sourceIds": [shelf.ids.lecture], "options": {"count": 5}})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert item["state"] == "done", item
    stems = [q["question"] for q in item["content"]["questions"]]
    assert len(stems) == 5 and len(set(stems)) == 5
    assert stems[:3] == [q["question"] for q in _FIRST]
    writes = [c for c in shelf.calls if "check" not in c]
    assert len(writes) == 2
    top_up = writes[1]["system"]
    assert "write exactly 2 new questions" in top_up and "2 questions that test" in top_up
    assert "벡터 시계의 칸 수는?" in top_up


async def test_a_quiz_whose_top_up_fails_keeps_what_it_has(shelf):  # noqa: F811
    shelf.answers.append(json.dumps({"title": "퀴즈", "questions": _FIRST}, ensure_ascii=False))
    shelf.answers.extend(["말로만", "또 말로만"])
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "quiz", "sourceIds": [shelf.ids.lecture], "options": {"count": 10}})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert item["state"] == "done" and len(item["content"]["questions"]) == 3


async def test_more_questions_than_asked_are_trimmed_to_the_count(shelf):  # noqa: F811
    shelf.answers.append(json.dumps({"title": "퀴즈", "questions": [*_FIRST, *_MORE[1:]]},
                                    ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "quiz", "sourceIds": [shelf.ids.lecture], "options": {"count": 4}})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert len(item["content"]["questions"]) == 4
    assert len([c for c in shelf.calls if "check" not in c]) == 1


_GUIDE = {"title": "논리 시계", "sections": [
    {"heading": "개요", "content": "분산 시스템에는 전역 시계가 없다 [1]."}]}


def _recording(monkeypatch) -> list[int]:
    caps: list[int] = []
    fake = shelf_calls.complete

    async def complete(user, model, system, prompt, max_tokens, effort="none"):
        if not system.startswith(("You proofread", "You fix sentences")):
            caps.append(max_tokens)
        return await fake(user, model, system, prompt, max_tokens, effort)

    monkeypatch.setattr(shelf_calls, "complete", complete)
    return caps


async def test_an_answer_cut_off_for_room_is_asked_again_with_more_room(shelf, monkeypatch):  # noqa: F811
    caps = _recording(monkeypatch)
    whole = json.dumps(_GUIDE, ensure_ascii=False)
    shelf.answers.extend([whole[:60], whole[:80], whole])
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "study_guide", "sourceIds": [shelf.ids.lecture]})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert item["state"] == "done", item
    base = studio_kinds.MAX_TOKENS["study_guide"]
    assert caps == [base, base * 2, 12_000]
    writes = [c for c in shelf.calls if "check" not in c]
    assert "ran out of room" in writes[1]["system"] and "more concisely" in writes[2]["system"]


def test_an_answer_that_used_all_its_room_counts_as_cut_off():
    assert studio_kinds is not None
    from app.services.studio import cut_off

    assert cut_off('{"title": "x", "sections": [{"heading"', {}, 4500)
    assert cut_off("말로만", {"outputTokens": 4400}, 4500)
    assert not cut_off("말로만 답함", {"outputTokens": 30}, 4500)


async def _add_lectures(ctx, names: list[str]) -> dict[str, str]:
    ids = {}
    async with ctx.maker() as db:
        for name in names:
            row = StoredFile(user_id=ctx.me.id, project_id=ctx.ids.project, name=name,
                             mime="application/pdf", text=f"# {name}\n클라우드 강의 내용입니다.",
                             tokens=20)
            db.add(row)
            await db.flush()
            ids[name] = row.id
        await db.commit()
    return ids


async def test_a_numbered_range_in_the_request_narrows_the_sources(shelf):  # noqa: F811
    ids = await _add_lectures(shelf, ["01강_클라우드 개요.pdf", "02강_가상화.pdf",
                                      "08강_쿠버네티스.pdf", "부록.pdf"])
    shelf.answers.append(json.dumps(_GUIDE, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "study_guide", "options": {"focus": "1강부터 2강까지 정리해 줘"}})
    assert started.status_code == 202, started.text
    body = started.json()
    # the lecture fixture's 「03강 논리 시계.pdf」 is outside the range
    assert sorted(body["sourceIds"]) == sorted([ids["01강_클라우드 개요.pdf"],
                                                ids["02강_가상화.pdf"]])
    assert body["options"]["scope"] == "1강부터 2강"
    await shelf_jobs.drain()
    write = shelf.calls[0]
    assert "08강" not in write["prompt"] and "03강" not in write["prompt"]
    assert "[1] 01강_클라우드 개요.pdf" in write["prompt"]
    assert "Scope: the reader asked for 1강부터 2강 only" in write["system"]


async def test_a_range_no_source_carries_reads_them_all(shelf):  # noqa: F811
    shelf.answers.append(json.dumps(_GUIDE, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "study_guide", "options": {"focus": "5~8장"}})
    body = started.json()
    assert len(body["sourceIds"]) == 2 and "scope" not in body["options"]
    await shelf_jobs.drain()


def test_ranges_and_numbered_names_are_read_with_their_unit():
    for focus, unit, low, high in [("1~7강", "lecture", 1, 7), ("5~8장", "chapter", 5, 8),
                                   ("3주차~6주차", "week", 3, 6), ("제1장~제3장", "chapter", 1, 3),
                                   ("chapters 3-5", "chapter", 3, 5),
                                   ("Week 2 to 4", "week", 2, 4)]:
        scope = studio_kinds.scope_of(focus)
        assert scope is not None and (scope.unit, scope.low, scope.high) == (unit, low, high)
    assert studio_kinds.scope_of("2024~2025년 자료") is None
    assert studio_kinds.scope_of("1강과 7강 비교") is None
    lectures = studio_kinds.scope_of("1~7강")
    assert studio_kinds.in_scope(lectures, "01강_개요.pdf")
    assert studio_kinds.in_scope(lectures, "lecture-07.pptx")
    assert not studio_kinds.in_scope(lectures, "12강 정리.pdf")
    # a chapter number is no lecture number; the title decides when the name carries none
    assert not studio_kinds.in_scope(lectures, "제3장.pdf")
    assert studio_kinds.in_scope(lectures, "slides.pdf", "", "3강 논리 시계")
    weeks = studio_kinds.scope_of("3주차~6주차")
    assert studio_kinds.in_scope(weeks, "week05.pdf")


# ── word spacing lost in a stored source ──────────────────────────────

GLUED = ("클라우드의장점은초기투자가없다는것이다클라우드는사용한만큼지불하는운영비(OPEX)구조이다 "
         "쿠버네티스는사용자가원하는상태(desired state)를계속유지하려고컨트롤러가동작한다")


def test_a_name_glossed_after_glued_words_is_no_misspelling():
    written = "클라우드는 사용한 만큼 지불하는 운영비(OPEX) 구조입니다 [1]."
    assert shelf_checks.renamed(written, GLUED) == []
    assert shelf_checks.script_problems(written, GLUED) == []
    assert shelf_checks.mend_script(written, GLUED) == written
    state = "쿠버네티스는 원하는 상태(desired state)를 유지합니다 [1]."
    assert shelf_checks.renamed(state, GLUED) == []
    assert shelf_checks.mend_script(state, GLUED) == state
    # a real misspelling of a word-sized name is still mended
    assert shelf_checks.mend_script("레이트(Raft)는 리더를 뽑는다.", "래프트(Raft)는 합의 방식") \
        == "래프트(Raft)는 리더를 뽑는다."


def test_glued_runs_are_no_vocabulary_and_spoil_a_rewrite():
    assert not any(len(v) > shelf_checks.WORD_MAX for v in shelf_checks._vocabulary(GLUED * 2))
    old = "클라우드는 사용한 만큼 지불하는 운영비(OPEX) 구조입니다 [1]."
    assert shelf_checks.spoils(old, "클라우드는 사용한 만큼 지불하는 "
                                    "클라우드는사용한만큼지불하는운영비(OPEX) 구조입니다 [1].")
    assert shelf_checks.spoils(old, "클라우드는사용한 만큼 지불하는 운영비(OPEX) 구조입니다 [1].")
    assert shelf_checks.spoils("원하는 상태를 유지한다.", "원하는 는원하는상태를 유지한다.")
    assert not shelf_checks.spoils(old, "클라우드는 쓴 만큼 내는 운영비(OPEX) 구조입니다 [1].")
    assert shelf_checks.unspaced(GLUED) and not shelf_checks.unspaced(old * 4)


async def test_a_rewrite_that_copies_glued_source_text_is_rejected():
    sentence = "클라우드는 사용한 만큼 지불하는 운영비(OPEX) 구조입니다 [1]."
    data = {"title": "가이드", "sections": [{"heading": "개요", "content": sentence}]}
    seen: dict[str, str] = {}

    async def proofread(system, prompt, most):
        return json.dumps({"1": "garbled: 어색함"}, ensure_ascii=False), {}

    async def write(system, prompt, most):
        seen["system"], seen["prompt"] = system, prompt
        bad = ("클라우드는 사용한 만큼 지불하는 클라우드는사용한만큼지불하는운영비(OPEX) "
               "구조입니다 [1].")
        return json.dumps({"items": [{"id": 0, "text": bad}]}, ensure_ascii=False), {}

    report = await shelf_checks.check(
        "study_guide", data, language="ko", by_number={1: GLUED}, names={1: "01강.pdf"},
        write=write, proofread=[proofread])
    assert data["sections"][0]["content"] == sentence
    assert report.fixed == 0 and report.details[0]["outcome"] == "kept: rewrite rejected"
    # the writer is told the passage lost its spacing
    assert "(spacing lost)" in seen["prompt"] and "spacing lost" in seen["system"]


async def test_glued_text_the_writer_copied_is_flagged_not_excused():
    sentence = "클라우드는사용한만큼지불하는운영비(OPEX) 구조입니다 [1]."
    data = {"title": "가이드", "sections": [{"heading": "개요", "content": sentence}]}

    async def proofread(system, prompt, most):
        return json.dumps({"1": "garbled: 클라우드는사용한만큼지불하는운영비"},
                          ensure_ascii=False), {}

    async def write(system, prompt, most):
        fixed = "클라우드는 사용한 만큼 지불하는 운영비(OPEX) 구조입니다 [1]."
        return json.dumps({"items": [{"id": 0, "text": fixed}]}, ensure_ascii=False), {}

    report = await shelf_checks.check(
        "study_guide", data, language="ko", by_number={1: GLUED}, names={1: "01강.pdf"},
        write=write, proofread=[proofread])
    assert report.garbled == 1 and report.fixed == 1
    assert data["sections"][0]["content"].startswith("클라우드는 사용한 만큼")


async def test_a_card_set_short_of_its_count_is_topped_up(shelf):  # noqa: F811
    def card(front: str, back: str) -> dict:
        return {"front": front, "back": back, "cite": [1]}

    first = [card("램포트 시계", "사건마다 정수를 붙여 순서를 정한다 [1]."),
             card("벡터 시계", "프로세스 수만큼의 칸을 가진다 [1]."),
             card("전역 시계", "분산 시스템에는 전역 시계가 없다 [1]."),
             card("뒤의 사건", "같은 프로세스에서 뒤의 사건은 더 큰 값을 받는다 [1].")]
    more = [card("램포트 시계", "사건마다 정수를 붙인다 [1]."),
            card("동시성 판단", "벡터 시계로는 두 사건이 동시에 일어났는지 판단할 수 있다 [1]."),
            card("인과 관계", "램포트 시계 값의 대소만으로는 인과 관계를 알 수 없다 [1].")]
    shelf.answers.append(json.dumps({"title": "카드", "cards": first}, ensure_ascii=False))
    shelf.answers.append(json.dumps({"title": "카드", "cards": more}, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "flashcards", "sourceIds": [shelf.ids.lecture], "options": {"count": 5}})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    fronts = [c["front"] for c in item["content"]["cards"]]
    assert fronts == ["램포트 시계", "벡터 시계", "전역 시계", "뒤의 사건", "동시성 판단"]
    top_up = [c for c in shelf.calls if "check" not in c][1]["system"]
    assert "write exactly 1 new cards" in top_up

"""The starting points: a sentence somebody begins from, before a shape.

A **design template** (`services.design_templates`) decides what the answer
comes out looking like. A starting point decides how the request opens — what
is being made, and what the person still has to supply. It is the shorter half
of the same idea, and until now it lived only in the frontend bundle.

It moved here because a starting point is no longer typed into the composer.
It is carried by the turn, the way an activated skill is: the message stays
the words the person wrote, and the template arrives beside it as its own
context block. That resolution happens on the server, so the catalogue it
resolves against has to be here too — an id only the client knows is an id the
server can only take on trust.

Written as literals rather than kept in a table, for the same reason the
rendering catalogue is read out of the image: these are versioned workflows
that ship with a release and change when the product changes, not rows anybody
edits at runtime. Somebody who wants a starting point of their own writes a
`templates` row, which is what that table is for, and both lists reach the
gallery in one shape.

The English half is declared and empty. The wire and the client's
fallback-to-Korean rule are worth having before the translations exist —
`design_templates` established exactly that pattern, and a card with a Korean
title in an English UI is readable, while a card waiting on a schema change is
not.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.chat import SessionKind


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One built-in starting point, named after the document it produces."""

    id: str
    #: The surface it starts. A starting point is a request, so its kind *is*
    #: the session kind — unlike a rendering template, whose `deck` has to be
    #: mapped onto the slides surface.
    kind: SessionKind
    #: The gallery's filter chip: 학업, 업무, 연구, 영업, 개발.
    group: str
    title: str
    #: What you get. One line, no feature list.
    description: str
    #: What you have to bring, shown as chips before anybody commits, and what
    #: the composer asks for in its placeholder once the card is attached.
    fills: tuple[str, ...]
    #: Ends mid-sentence, where the person takes over. That is the whole shape
    #: of a starting point: it says what is being made and hands the turn back.
    prompt: str
    #: The English half. Empty until somebody writes it; the client falls back
    #: to the Korean, which leaves a card readable rather than blank.
    title_en: str = ""
    description_en: str = ""
    fills_en: tuple[str, ...] = ()
    prompt_en: str = ""


_ALL: tuple[PromptTemplate, ...] = (
    # A starting point is a small workflow, not a decorative example prompt.
    # Each one states what to inspect, how to handle evidence, what the answer
    # must contain, and what "done" means. The person's material arrives after
    # this framing as a separate message, so none of these ends mid-sentence.
    # ── chat: learn, inspect, decide, and write ─────────────────────────
    PromptTemplate(
        id="t_translate",
        kind=SessionKind.chat,
        group="학업",
        title="전공 원문 읽기",
        description="문단별 뜻을 풀고 핵심 주장과 전공 용어를 정리합니다",
        fills=("원문", "전공 분야", "읽는 목적", "원하는 깊이"),
        prompt=(
            "첨부하거나 붙여 넣은 전공 원문을 정밀하게 읽는다. 먼저 문서의 주장과 "
            "구조를 요약하고, 문단별로 자연스러운 번역과 해설을 제공한다. 전공 용어는 "
            "원어·번역어·이 문맥에서의 뜻을 표로 정리한다. 직역이 여러 가지인 표현은 "
            "하나로 단정하지 말고 대안을 설명한다. 원문에 없는 사실을 보충하지 말며, "
            "마지막에는 이해를 확인할 질문 3개와 더 읽어야 할 대목을 제시한다."
        ),
    ),
    PromptTemplate(
        id="t_debug",
        kind=SessionKind.chat,
        group="개발",
        title="장애 원인 좁히기",
        description="로그와 변경 이력으로 원인을 좁히고 확인 순서와 복구안을 정합니다",
        fills=("에러 로그", "재현 조건", "환경·버전", "최근 변경", "영향 범위"),
        prompt=(
            "장애를 추측으로 단정하지 말고 진단 절차로 좁힌다. 관찰된 사실과 추론을 "
            "분리하고, 가능한 원인을 가능성·영향도 순으로 표로 정리한다. 각 가설마다 "
            "확인 명령이나 로그 위치, 기대 결과, 틀렸을 때 다음 단계를 적는다. 즉시 "
            "피해를 줄이는 완화책과 근본 수정안을 나누고, 되돌리기 조건과 수정 후 검증 "
            "항목을 포함한다. 비밀값과 개인정보는 출력하지 않는다."
        ),
    ),
    PromptTemplate(
        id="t_schema",
        kind=SessionKind.chat,
        group="개발",
        title="SQL 작성과 검증",
        description="스키마와 지표 정의를 바탕으로 쿼리와 검증용 쿼리를 만듭니다",
        fills=("DB 종류·버전", "테이블 구조", "지표 정의", "필터·기간", "예상 규모"),
        prompt=(
            "요청한 지표의 분자·분모·기간·중복 제거 기준을 먼저 명시하고 SQL을 작성한다. "
            "스키마에 없는 컬럼은 만들지 않는다. 조인 키와 카디널리티, NULL 처리, 시간대, "
            "경계 날짜를 설명한다. 실행 쿼리 뒤에 작은 표본으로 결과를 검증할 대조 쿼리와 "
            "성능상 필요한 인덱스 또는 실행 계획 확인점을 제시한다. 위험한 변경 쿼리는 "
            "기본적으로 트랜잭션과 롤백 절차를 포함한다."
        ),
    ),
    PromptTemplate(
        id="t_email",
        kind=SessionKind.chat,
        group="업무",
        title="업무 메일 작성",
        description="받는 사람이 용건과 요청 사항, 기한을 바로 알 수 있게 씁니다",
        fills=("받는 사람과 관계", "보내는 목적", "요청할 행동", "기한", "반드시 넣을 사실"),
        prompt=(
            "업무 메일을 제목과 본문으로 작성한다. 첫 문장에서 용건을 밝히고, 배경은 "
            "결정에 필요한 만큼만 쓴다. 수신자가 해야 할 행동·기한·회신 방법을 눈에 띄게 "
            "구분한다. 제공되지 않은 약속이나 날짜는 만들지 않는다. 관계에 맞는 존칭을 "
            "사용하되 과장된 인사말은 피하고, 마지막에 보내기 전 확인할 빈칸을 표시한다."
        ),
    ),
    PromptTemplate(
        id="t_meeting_prep",
        kind=SessionKind.chat,
        group="영업",
        title="미팅 준비",
        description="최신 공개 정보와 지난 접촉 내용을 바탕으로 질문과 예상 답변을 준비합니다",
        fills=("고객사·참석자", "미팅 목적", "지난 접촉", "제안 내용", "기준일"),
        prompt=(
            "고객 미팅 브리프를 만든다. 웹 검색이 가능하면 회사·참석자·최근 사업 변화에 "
            "관한 최신 공개정보를 기준일과 출처 링크와 함께 확인한다. 확인된 사실과 내부 "
            "추정을 분리한다. 상대가 물을 질문과 예상 반론, 우리가 확인할 질문, 보여 줄 "
            "근거를 표로 정리한다. 30분 아젠다와 미팅이 성공했다고 판단할 조건, 미팅 직후 "
            "보낼 후속 조치까지 제시한다."
        ),
    ),
    PromptTemplate(
        id="t_compare",
        kind=SessionKind.chat,
        group="업무",
        title="문서 변경 사항 비교",
        description="두 자료에서 달라지거나 서로 어긋나는 내용을 찾아 영향까지 정리합니다",
        fills=("기준 문서", "비교 문서", "중요하게 볼 항목", "적용 시점"),
        prompt=(
            "두 자료를 항목·조항·수치 단위로 대조한다. 동일한 내용은 생략하고 추가·삭제·변경·"
            "충돌로 분류한다. 각 차이에 두 문서의 정확한 위치와 원문 요지를 붙이고, 업무상 "
            "영향과 확인이 필요한 담당자를 적는다. 표현만 달라진 경우와 의미가 달라진 경우를 "
            "구분한다. 마지막에 즉시 반영, 검토 필요, 영향 없음으로 후속 조치 목록을 만든다."
        ),
    ),
    PromptTemplate(
        id="t_fact_check",
        kind=SessionKind.chat,
        group="조사",
        title="사실 확인",
        description="확인할 주장을 나누고 최신 1차 자료에서 근거를 찾습니다",
        fills=("확인할 주장", "기준일", "적용 지역", "허용할 출처"),
        prompt=(
            "확인할 문장을 검증 가능한 개별 주장으로 나눈다. 웹 검색을 사용해 가능한 한 "
            "정부·기관·원문 논문·기업 공시 같은 1차 자료를 우선 확인하고, 각 주장에 사실·"
            "대체로 사실·맥락 필요·근거 부족·거짓 중 하나를 판정한다. 출처 제목·발행일·링크와 "
            "근거가 되는 대목을 짧게 요약한다. 최신성이 중요한 값은 기준일을 명시하고, 서로 "
            "충돌하는 자료는 숨기지 않는다."
        ),
    ),
    PromptTemplate(
        id="t_concept_tutor",
        kind=SessionKind.chat,
        group="학업",
        title="개념 학습",
        description="현재 수준에 맞춰 개념과 예시를 배우고 문제로 이해도를 확인합니다",
        fills=("배울 개념", "현재 수준", "학습 목적", "사용 가능한 시간"),
        prompt=(
            "학습자의 수준에 맞춰 개념을 가르친다. 먼저 선수 지식을 짧게 진단한 뒤 직관, "
            "정확한 정의, 대표 예제, 반례 순서로 설명한다. 새 용어는 사용하기 전에 정의하고, "
            "흔한 오개념을 왜 틀렸는지 보여 준다. 중간마다 답을 바로 공개하지 않는 확인 문제를 "
            "주고, 마지막에는 핵심 요약·연습문제 3개·다음 학습 경로를 제시한다."
        ),
    ),
    PromptTemplate(
        id="t_code_review",
        kind=SessionKind.chat,
        group="개발",
        title="코드 변경 검토",
        description="바뀐 코드의 오류와 보안·성능 문제를 찾아 수정 방법을 제안합니다",
        fills=("변경 코드·diff", "언어·런타임", "의도", "테스트 결과", "운영 제약"),
        prompt=(
            "코드 변경을 의도와 실제 동작이 일치하는지 검토한다. 정확성, 경계 조건, 동시성, "
            "보안, 성능, 관측 가능성, 하위 호환성 순으로 확인한다. 문제마다 심각도·근거 위치·"
            "재현 조건·최소 수정안을 적고, 취향 차이는 결함과 분리한다. 통과한 부분도 명시하고, "
            "추가할 테스트를 정상·경계·실패 경로로 나눠 제안한다."
        ),
    ),
    PromptTemplate(
        id="t_data_review",
        kind=SessionKind.chat,
        group="연구",
        title="데이터 분석 설계 점검",
        description="변수 정의와 결측값, 편향, 검정 방법을 분석 전에 살펴봅니다",
        fills=("연구 질문", "데이터 설명", "변수 정의", "표본 추출", "예정한 분석"),
        prompt=(
            "분석을 실행하기 전에 설계를 검토한다. 연구 질문과 관측 단위, 결과·설명 변수, "
            "표본 포함 기준을 명확히 하고 결측·이상치·중복·누출 가능성을 점검한다. 예정한 "
            "통계 방법의 가정과 위반 시 대안을 적고, 다중 검정·효과크기·불확실성 보고 방식을 "
            "제안한다. 필요한 검증 표와 재현 가능한 분석 순서를 체크리스트로 마무리한다."
        ),
    ),

    # ── report: the job is independent from the document's visual shape ─
    PromptTemplate(
        id="t_report_literature",
        kind=SessionKind.report,
        group="연구",
        title="문헌 동향 조사",
        description="검색 방법과 선정 기준을 밝히고 주요 쟁점과 연구 공백을 정리합니다",
        fills=("연구 질문", "기간·언어", "포함·제외 기준", "중점 분야", "인용 양식"),
        prompt=(
            "문헌 동향 보고서를 작성한다. 먼저 검색 데이터베이스·검색어·기준일·포함 및 제외 "
            "기준을 밝힌다. 원문 논문과 공식 데이터베이스를 우선해 주요 연구를 방법·표본·결과·"
            "한계로 비교한다. 단순 논문 나열이 아니라 합의된 점, 충돌하는 결과, 방법론 변화, "
            "아직 답하지 못한 질문으로 종합한다. 모든 사실 주장에 확인 가능한 인용과 링크를 "
            "붙이고, 검색 한계와 후속 연구 질문을 명시한다."
        ),
    ),
    PromptTemplate(
        id="t_report_trend",
        kind=SessionKind.report,
        group="조사",
        title="정책·산업 동향 조사",
        description="최근 변화를 조사하고 예상 영향과 앞으로 살펴볼 지표를 정리합니다",
        fills=("주제·시장", "대상 지역", "조사 기간", "읽는 사람", "결정할 사안"),
        prompt=(
            "기준일 현재 정책 또는 산업 동향을 조사해 의사결정용 보고서를 작성한다. 법령·정부 "
            "발표·공시·통계·원문 보고서를 우선하고 기사만으로 핵심 수치를 확정하지 않는다. "
            "변화의 타임라인, 주요 행위자, 확정된 사실과 전망, 우리에게 미칠 영향, 낙관·기준·"
            "비관 시나리오를 구분한다. 수치마다 기준 시점과 출처 링크를 붙이고 앞으로 확인할 "
            "선행 지표와 권고 행동을 제시한다."
        ),
    ),
    PromptTemplate(
        id="t_report_executive",
        kind=SessionKind.report,
        group="업무",
        title="의사결정 보고서",
        description="결정할 사안과 대안, 근거, 위험을 앞부분에서 한눈에 보여 줍니다",
        fills=("결정할 사안", "의사결정자", "대안", "핵심 근거", "기한·제약"),
        prompt=(
            "경영진이 짧은 시간에 결정할 수 있는 보고서를 만든다. 첫 페이지에 결론, 필요한 "
            "결정, 권고안, 비용·효과·주요 위험을 요약한다. 대안은 같은 기준과 같은 기간으로 "
            "비교하고 아무것도 하지 않는 선택도 포함한다. 제공되지 않은 수치는 만들지 말고 "
            "근거와 가정을 분리한다. 권고안을 뒤집어야 할 조건, 책임자, 결정 기한과 결정 후 "
            "첫 행동을 명시한다."
        ),
    ),
    PromptTemplate(
        id="t_report_research_plan",
        kind=SessionKind.report,
        group="대학원",
        title="연구계획서",
        description="연구 질문부터 선행연구, 방법, 분석, 윤리, 일정까지 계획합니다",
        fills=("연구 주제", "핵심 질문", "대상·자료", "방법", "제출 요건"),
        prompt=(
            "연구계획서를 작성한다. 문제 제기에서 연구 질문, 선행연구의 공백, 자료와 방법, "
            "분석 계획이 논리적으로 이어지게 한다. 표본 선정·변수 정의·타당도·예상 한계·"
            "연구윤리와 데이터 관리 계획을 포함한다. 확인하지 않은 선행연구를 만들어 인용하지 "
            "않으며, 검색한 자료에는 링크와 서지정보를 붙인다. 단계별 산출물과 현실적인 일정, "
            "실패 가능성과 대안을 표로 정리한다."
        ),
    ),
    PromptTemplate(
        id="t_report_analysis",
        kind=SessionKind.report,
        group="연구",
        title="데이터 분석 보고서",
        description="데이터 처리 과정과 분석 결과, 한계, 재현 방법을 보고서로 정리합니다",
        fills=("분석 질문", "데이터 파일", "변수 정의", "분석 기준", "독자"),
        prompt=(
            "첨부 데이터에 근거한 분석 보고서를 작성한다. 관측 단위와 표본, 변수 정의, 제외 "
            "기준과 전처리 내역을 먼저 밝힌다. 기술통계와 핵심 비교를 재현 가능한 계산으로 "
            "수행하고 표·그래프에는 분모·단위·기간을 표시한다. 상관을 인과로 표현하지 않으며 "
            "불확실성, 민감도, 데이터 한계를 함께 보고한다. 결론은 분석 질문에 직접 답하고 "
            "재현에 필요한 코드·절차와 다음 수집 항목을 남긴다."
        ),
    ),
    PromptTemplate(
        id="t_report_project",
        kind=SessionKind.report,
        group="업무",
        title="프로젝트 실행 계획",
        description="목표와 산출물, 일정, 담당자, 위험, 완료 기준을 구체적으로 정합니다",
        fills=("배경·목표", "범위", "참여자", "기한·예산", "제약"),
        prompt=(
            "실행을 승인받을 수 있는 프로젝트 계획서를 작성한다. 해결할 문제와 측정 가능한 "
            "목표, 포함·제외 범위, 산출물별 완료 기준을 분리한다. 작업 분해 구조에 책임자·"
            "선행 조건·마감·검토자를 붙이고 주요 의사결정 지점을 표시한다. 위험은 가능성·영향·"
            "조기 신호·대응·담당으로 정리한다. 예산과 인력이 제공되지 않았으면 추정치를 사실처럼 "
            "쓰지 말고 확인 항목으로 남긴다."
        ),
    ),
    PromptTemplate(
        id="t_report_incident",
        kind=SessionKind.report,
        group="공대",
        title="장애 사후 분석",
        description="장애 영향과 시간대별 대응, 근본 원인, 재발 방지책을 기록합니다",
        fills=("영향 범위", "타임라인", "로그·지표", "최근 변경", "조치 내역"),
        prompt=(
            "비난 없는 장애 사후 분석서를 작성한다. 확인된 사실만으로 영향과 정확한 시간대를 "
            "요약하고, 당시 알고 있던 것과 사후에 밝혀진 것을 구분한 타임라인을 만든다. 직접 "
            "원인에서 시스템적 근본 원인과 탐지·대응이 늦어진 이유까지 추적한다. 사람 이름 대신 "
            "역할을 쓰고, 재발 방지 항목마다 우선순위·담당·기한·검증 방법을 붙인다. 모르는 "
            "부분은 미확인으로 남긴다."
        ),
    ),

    # ── slides: the story to tell, separately from its visual treatment ─
    PromptTemplate(
        id="t_slides_seminar",
        kind=SessionKind.slides,
        group="대학원",
        title="논문 세미나 발표",
        description="연구 질문과 방법, 핵심 결과, 한계, 토론할 내용을 발표로 구성합니다",
        fills=("논문 원문", "청중 수준", "발표 시간", "중점 논점"),
        prompt=(
            "논문 세미나 발표를 구성한다. 서지정보와 연구 질문을 먼저 밝히고, 방법은 결과를 "
            "해석하는 데 필요한 만큼만 설명한다. 핵심 표·그림마다 무엇을 보여 주는지와 무엇을 "
            "보여 주지 못하는지를 한 장씩 정리한다. 저자의 주장과 발표자의 평가를 구분하고, "
            "재현성·타당도·일반화 한계를 포함한다. 발표 시간에 맞춰 장수를 제한하고 마지막에 "
            "토론 질문 3개와 추가 확인 자료를 제시한다."
        ),
    ),
    PromptTemplate(
        id="t_slides_defense",
        kind=SessionKind.slides,
        group="대학원",
        title="학위·중간 심사 발표",
        description="연구의 기여와 근거, 한계, 남은 계획을 심사 발표로 구성합니다",
        fills=("연구 질문", "핵심 결과", "기여", "한계", "발표 시간·심사 단계"),
        prompt=(
            "심사위원이 연구의 타당성과 기여를 판단할 발표를 만든다. 문제와 연구 질문, 기존 "
            "연구와의 차이, 방법 선택의 이유, 핵심 결과, 기여, 한계를 논리적으로 연결한다. "
            "주장마다 대응하는 결과나 출처가 있어야 하며 결과가 없는 주장은 약속으로 표현한다. "
            "예상 질문과 방어 근거를 발표자 노트에 넣고, 중간 심사라면 남은 위험·일정·완료 "
            "기준으로 끝낸다."
        ),
    ),
    PromptTemplate(
        id="t_slides_lecture",
        kind=SessionKind.slides,
        group="교육",
        title="강의 자료 만들기",
        description="학습 목표에 맞춰 설명과 예시, 확인 문제, 마무리 활동을 구성합니다",
        fills=("주제", "학습자 수준", "수업 시간", "선수 지식", "평가 방식"),
        prompt=(
            "수업이 끝난 뒤 학습자가 할 수 있어야 하는 행동형 목표 2~4개를 먼저 정한다. "
            "개념마다 직관, 정확한 정의, 이미 아는 사례, 반례 또는 오개념, 짧은 확인 문제를 "
            "배치한다. 한 장에 한 학습 단계만 두고 답은 다음 장이나 발표자 노트에 둔다. 새 "
            "용어를 정의 전에 사용하지 않으며, 수업 시간에 맞춰 활동과 설명 시간을 배분한다. "
            "마지막에 목표별 회고와 후속 연습을 제시한다."
        ),
    ),
    PromptTemplate(
        id="t_slides_briefing",
        kind=SessionKind.slides,
        group="업무",
        title="의사결정 브리핑",
        description="결정할 사안과 대안, 권고안, 요청 사항을 짧은 발표로 정리합니다",
        fills=("결정할 사안", "참석자", "대안", "권고안", "발표 시간"),
        prompt=(
            "회의에서 결론을 내기 위한 브리핑을 만든다. 첫 두 장 안에 결정할 사안과 권고안을 "
            "밝힌다. 현황은 결정에 영향을 주는 사실만 남기고, 대안은 비용·효과·위험·가역성·"
            "기간 같은 동일 기준으로 비교한다. 제공되지 않은 수치는 만들지 않는다. 반대 의견과 "
            "권고안을 바꿀 조건을 포함하고, 마지막 장에 누가 무엇을 언제까지 결정해야 하는지 "
            "명시한다."
        ),
    ),
    PromptTemplate(
        id="t_slides_proposal",
        kind=SessionKind.slides,
        group="영업",
        title="고객 제안 발표",
        description="고객의 문제에서 시작해 해결 방법과 도입 계획, 요청 사항을 설명합니다",
        fills=("고객·청중", "확인된 문제", "제안", "근거", "예산·일정"),
        prompt=(
            "고객이 도입 여부를 판단할 제안 발표를 만든다. 고객이 확인해 준 문제와 공개적으로 "
            "검증한 사실에서 시작하고, 제품 기능 나열 대신 업무가 어떻게 달라지는지 보여 준다. "
            "현행과 제안을 같은 기준으로 비교하며 효과 수치는 근거·가정·산식을 함께 밝힌다. "
            "도입 단계, 고객이 준비할 것, 위험과 되돌리기 방안을 포함한다. 마지막 장에는 결정 "
            "주체·요청 사항·기한·다음 미팅을 명시한다."
        ),
    ),
    PromptTemplate(
        id="t_slides_project",
        kind=SessionKind.slides,
        group="업무",
        title="프로젝트 현황 공유",
        description="완료한 일과 변경 사항, 막힌 일, 필요한 결정을 공유합니다",
        fills=("프로젝트 목표", "보고 기간", "완료한 것", "막힌 것", "결정 요청"),
        prompt=(
            "프로젝트 상태를 의사결정 중심으로 공유한다. 원래 목표와 이번 기간에 실제 완료된 "
            "산출물을 먼저 대비하고, 단순 진행률 대신 완료 증거와 미완료 이유를 적는다. 일정·"
            "범위·비용 변경, 주요 위험과 조기 신호, 막힌 사안과 풀 수 있는 사람을 명확히 한다. "
            "결정이 필요한 것은 선택지와 마감일을 붙이고, 마지막에는 다음 기간의 책임자별 "
            "행동과 완료 기준을 제시한다."
        ),
    ),
)

#: By id, in catalogue order. Built once, because the catalogue is a constant
#: of this image: rebuilding it per request would be work for a dict that
#: cannot have changed since the process started.
_TEMPLATES: dict[str, PromptTemplate] = {t.id: t for t in _ALL}


def all_templates() -> list[PromptTemplate]:
    return list(_TEMPLATES.values())


def get(template_id: str | None) -> PromptTemplate | None:
    return _TEMPLATES.get(template_id or "")


__all__ = ["PromptTemplate", "all_templates", "get"]

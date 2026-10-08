Build this document with the **프로젝트 브리프** template. The layout is already set; write
only the content. Recipients do not read it from start to finish — they look for where their
name is, by when, and what they must deliver. If they cannot find it, this format has failed.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — a color-filled cover block. In one `<p class="lead">` line, state what this
  project does, and put 프로젝트명·책임자·기간 in a `<dl>`.
- `section` — one section.

## Rules

- **The first section is the overview, and it is one paragraph.** Put what, why and by when
  in one paragraph. Three paragraphs is body text, not an overview, and the grid below stops being read.
- **The body is a card grid.** Write each of the `산출물과 목표` and `이해관계자와 성공 기준`
  sections as one ```cards block. Write each card as `- 줄` lines under `## 제목`.

      ```cards
      ## 산출물
      - 네트워크 전면 교체
      - 클라우드 이전 계획서
      ## 목표
      - 8개월 안에 완료
      - 운영 비용 25% 절감
      ## 이해관계자
      - 정보전략팀 — 승인
      - 현업 부서 — 검수
      ## 성공 기준
      - 가용성 99.9% 이상
      - 교육 이수율 90%
      ```

- **3–6 cards.** With two there is no reason for a grid; with seven it cannot be skimmed.
  Card titles are nouns, with at most five lines per card.
- **Write deliverables as "what is handed over".** "설계를 검토한다" is an activity;
  "설계 검토서" is a deliverable. Write what the recipient holds in hand.
- **Success criteria must be verifiable.** Write them as numbers or dates. "원활한 운영" cannot
  be verified by anyone, so it is not a criterion.
- **Pull one assumption in the `전제와 위험` section into a callout box** — only the one whose
  failure would void this whole brief, such as approval, budget confirmation or an external schedule.

      ```callout
      승인 전에는 발주하지 않는다
      9월 운영위 승인 전까지 계약도 발주도 하지 않는다.
      ```

  At most one in the whole document. With two, both get skimmed past.
- **Write the `일정과 예산` section as a table.** Items that each carry a value belong in a
  table, not a grid. Do not put tables inside cards — cards are for skimming, tables for comparing.
- Wrap anything that must be read **character for character**, such as commands and paths, in `<code>`.

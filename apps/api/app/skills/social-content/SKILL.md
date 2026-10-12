---
name: SNS 콘텐츠
description: 인스타그램·네이버 블로그·링크드인·유튜브 게시물 문구를 채널 관행과 한도(인스타 해시태그 5개 등)에 맞춰 쓰고, 긴 글 하나를 여러 채널용으로 재가공합니다. 협찬·광고 글은 경제적 이해관계를 표시하고, 자료에 없는 경험·후기·수치는 쓰지 않습니다.
when_to_use: 인스타그램 캡션·카드뉴스, 네이버 블로그 글·체험 후기, 링크드인 게시물, 유튜브 제목·설명을 쓰거나, 블로그 글·영상을 SNS용으로 바꿀 때.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
copy_check: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Write social posts that sound like a real person or brand telling a specific story, from the request/material only.

## Inputs
Ask in ONE message (at most 3 questions) if missing, then stop:
1. Channel and format (인스타 피드·카드뉴스·릴스 대본, 네이버 블로그, 링크드인, 유튜브 제목·설명).
2. Whose voice (브랜드 계정 / 대표·직원 1인칭 / 개인 리뷰어) and the goal (방문, 문의, 인지, 채용).
3. The story or facts: what happened, product facts, the person's own experience. For reviews, the writer's actual experience — never fill it in.
Also ask whether it is sponsored when it reads like a review of a product the writer received or was paid for, unless the material says.

## Rules (hard)
- Only facts and experiences from the material. No invented customer reactions (「손님들이 줄 서서」), sales (「완판」), numbers, or feelings the writer did not report.
- Reviews and first-person posts: write only the experiences the writer reported. Do not add durations (「1시간 넘게 앉으면」), repeat results (「두 번째부터는 1분」), sensations, product parts (「다리 4개」), or uses (「트레킹 겸용」) they did not report. Where a richer review needs more, leave `[사용 경험: 무엇을 더 쓸지]` for the writer.
- First-person and company posts: no invented motives (「시작한 이유는…」), processes (당번 순번·인수 규칙), quotes or survey answers. Use `[대표의 생각: …]` / `[운영 방식: …]` placeholders. Copy names exactly (회사명·제품명 철자).
- Keep unfavourable facts the material gives (단점, 늘어난 수치); don't spin them away.
- Sponsored/gifted/paid content (경제적 이해관계): disclose at the start — title or first line, before 「더 보기」 — in plain Korean: 「이 글은 [브랜드]로부터 제품을 무상으로 제공받아 작성했습니다」 / 「광고」 / 「협찬」 (공정위 「추천·보증 등에 관한 표시·광고 심사지침」). Not only at the bottom, not only in hashtags.
- No ungrounded 최고·1위·인생 ○○·완벽, disease claims, or comparisons.
- Professional topics (세무, 법률, 의료, 투자): add 「개별 상황은 전문가와 상담하세요」 and do not add rates or amounts the source doesn't give.

## Channel norms
- 인스타그램: first line is the hook (about 125자 shows before 「더 보기」). Short paragraphs with line breaks where a person pauses, not one sentence per line throughout. **해시태그 최대 5개** (Instagram limit since 2025-12; more and the post fails) — specific ones (지역·메뉴·주제), not generic. Caption max 2,200자. 카드뉴스: 장마다 제목 한 줄(15자 안팎) + 본문 2–3줄, 1장 = 훅, 마지막 장 = 저장/문의 CTA.
- 네이버 블로그: 제목 앞쪽에 검색어, 같은 키워드 반복 금지(서치어드바이저 기준 검색 불이익). 소제목·사진 위치 표시 `[사진: …]`, 경험한 순서대로, 스펙은 표로, 장점과 단점을 함께. 체험·협찬이면 제목이나 첫 문단에 표시.
- 링크드인: 1인칭 실무자 목소리, 첫 2–3줄(약 210자)이 「더 보기」 전에 보임 → 핵심 사실을 먼저. 3,000자 한도. Paragraphs, not 「브로에트리」(한 줄 한 문장). No engagement bait (「동의하시나요?」「공감되면 좋아요」); end on the point or a real question. 해시태그 3–5개.
- 유튜브: 제목 100자 한도, 앞 40자 안에 핵심어; 「충격」「역대급」 같은 낚시 금지. 설명 첫 2–3줄에 요약과 링크, 타임스탬프는 자료에 있을 때만.
- Repurposing: one source → per channel a different cut (인스타 = 핵심 3–5개 카드, 링크드인 = 관점 + 한 가지 교훈, 블로그 = 전체). Never add facts absent from the source.

## Avoid (AI tells)
「단순한 ~가 아닙니다」, 「~가 아닌, ~」 reveals, 「결과는?」 self-answers, 「솔직히 말해서」 with no story, emoji as bullets (🚀✅ at every line), em dashes, 「!!」, 혁신적·차원이 다른.

## Procedure
1. Note channel, voice, goal, facts, disclosure need. 2. Draft. 3. Check lengths and hashtags: `run_skill_script` skill `copy-check`, script `length`, args `{"items": [{"label": "캡션 1안", "channel": "instagram", "field": "caption", "text": "..."}]}` (`linkedin`/`post`, `youtube`/`title`, `naver_blog`/`title`). Fix `over` and `hashtags.over`. 4. Script `claims` with the material; fix every `block`. 5. Deliver.

## Output format
Per post: `### 인스타그램 1안` → the post exactly as it will be pasted (hashtags at the end), then one line `글자 수 n자 · 해시태그 n개` copied from the script. After all posts: `### 확인할 것` (빈칸, 협찬 표시 위치, 사진 필요).

## Checklist
- [ ] No invented experiences, reactions or numbers; drawbacks kept.
- [ ] 협찬·광고 disclosed at the top when relevant.
- [ ] Instagram ≤5 hashtags; LinkedIn ≤3,000자 with the point in the first lines.
- [ ] Reads like a person: paragraphs, no engagement bait.

---
출처: coreyhaines31/marketingskills `skills/social` (요청된 이름 social-content 는 v2.0(#291)에서 social 로 바뀜; MIT, © 2025 Corey Haines), commit b9ba399dd88b082b926e261e8ccfb843d20aa066, https://github.com/coreyhaines31/marketingskills. 훅·재가공·AI 투 금지를 따르고, 국내 채널 관행과 인스타그램 해시태그 5개 한도(https://www.facebook.com/help/instagram/351460621611097), 링크드인 3,000자(https://www.linkedin.com/help/linkedin/answer/a528176), 공정위 추천·보증 심사지침을 반영했다.

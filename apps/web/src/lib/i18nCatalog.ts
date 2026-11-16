/** English for the shipped skills' 「이럴 때 씁니다」 lines and the 공개 전 점검 bundle (merged into `EN`). */
export const EN_CATALOG: Record<string, string> = {
  '공개 전 점검': 'Pre-publication check',
  '자료에서 근거를 찾지 못한 답은 보류하고 담당 부서를 안내하며, 답에 들어간 개인정보를 가립니다.':
    'Holds back answers the material does not support, points to the responsible department, and masks personal data in the answer.',
  '써 둔 보고서·공문·안내문·메일·소개글·자기소개서를 교정하거나 다듬어 달라고 할 때. 「맞춤법 봐 줘」「문장 다듬어 줘」「어투 통일해 줘」「교정해 줘」. 상투어·군더더기를 걷어내는 일은 「상투어 걷어내기」, 새로 쓰는 일은 다른 스킬.':
    'When asked to proofread or polish a report, official letter, notice, email, bio or cover letter already written: "check my spelling", "polish these sentences", "make the tone consistent", "proofread this". Removing clichés and filler is for "Strip the clichés"; writing something new is for other skills.',
  '주어진 자료로 쓴 문서나 답을 내기 전에, 자료에 없는 값을 지어 넣지 않았는지 확인할 때. 서버가 붙은 에이전트·서식에서는 쓰고 나서 자동으로 돌린다.':
    'Before handing over a document or answer written from given material, to check that no value absent from the material was invented. In agents and formats the server attaches it to, it runs automatically after writing.',
  '시장·경쟁 조사 결과를 내기 전 마지막 확인. 「기업 조사」「경쟁사 분석」 스킬이 부른다. 직접 쓴 조사 글의 출처가 빠짐없는지 볼 때도.':
    'The last check before market or competitor research goes out. The company research and competitor analysis skills call it. Also for checking that sources are complete in research text you wrote yourself.',
  '제품·업체·입지·운영 방식처럼 여러 안 가운데 하나를 골라야 하는 보고서가 필요할 때.':
    'When you need a report for choosing one option among several, such as a product, vendor, location or way of operating.',
  '전 직원·특정 부서에 알리는 공지, 제도·규정·근무 방식 변경 안내, 시스템 점검·이전 안내, 팀 주간 보고(Progress·Plans·Problems), 사내 FAQ를 쓸 때. 「공지문 써 줘」「변경 안내 메일」「점검 공지」「주간 3P」.':
    'When writing an announcement to all staff or a department, a notice of a policy, rule or working-style change, a system maintenance or migration notice, a weekly team report (Progress, Plans, Problems) or an internal FAQ: "write a notice", "change announcement email", "maintenance notice", "weekly 3P".',
  '「심층 조사해 줘」「관련 연구·사례를 깊이 찾아 줘」「동향 리포트」처럼 검색 몇 번으로는 부족한 조사를 부탁할 때. 간단한 사실 확인은 웹 검색으로 충분하다.':
    'For research a few searches cannot cover: "research this in depth", "find related studies and cases", "trend report". A web search is enough for a simple fact check.',
  '견적서·제안서의 금액 표를 만들거나 고칠 때, 부가세 포함·별도가 섞인 업체 견적을 견줄 때, 전제가 바뀐 시나리오별 합계가 필요할 때.':
    'When building or fixing the price table of a quote or proposal, comparing vendor quotes that mix VAT-included and VAT-excluded prices, or needing totals per scenario when assumptions change.',
  '써 둔 글이 뻔하고 밋밋하다, 「AI가 쓴 것 같다」, 「군더더기 빼 줘」「구체적으로 바꿔 줘」「번역투 고쳐 줘」라고 할 때. 소개글·보고서·제안서·보도자료·자기소개서·광고 문구 모두. 맞춤법 교정은 「교정·윤문」.':
    'When the text reads as generic and flat, "sounds written by AI", "cut the filler", "make it specific", "fix the translated tone". For bios, reports, proposals, press releases, cover letters and ad copy alike. For spelling and grammar, use the proofreading skill.',
  '미팅·영업·투자·제휴·입찰 전에 상대 회사를 빠르게 파악할 때, 「○○ 회사 조사해 줘」「기업 개요 정리」. 별칭 company-research.':
    'To get up to speed on a company before a meeting, sale, investment, partnership or bid: "research company X", "summarize the company profile". Alias: company-research.',
  '표를 엑셀 파일로 받고 싶을 때, 합계·비율 같은 열을 수식으로 넣은 엑셀이 필요할 때, CSV를 서식 있는 엑셀로 바꿀 때.':
    'When you want a table as an Excel file, need Excel with columns such as totals and ratios as formulas, or want a CSV turned into a formatted workbook.',
  '첨부한 엑셀·CSV로 합계·비율·기간 비교 표가 필요할 때, 데이터를 정리하고 무엇을 지웠는지 남겨야 할 때.':
    'When an attached Excel or CSV file should become a table of totals, ratios or period comparisons, or the data must be cleaned with a record of what was removed.',
  '경쟁 구도 파악, 사업계획서·IR 의 경쟁 분석, 포지셔닝·가격 결정 전, 「경쟁사 분석」「경쟁사 비교표」「경쟁 지형」. 별칭 competitor-analysis.':
    'To map the competitive landscape, for the competitor section of a business plan or IR deck, and before positioning or pricing: "competitor analysis", "competitor comparison table", "competitive landscape". Alias: competitor-analysis.',
  '사용자가 YouTube 링크를 주고 「이 영상 요약해 줘」「무슨 내용이야」「자막 뽑아 줘」「강의 핵심 정리」를 부탁할 때.':
    'When someone gives a YouTube link and asks: "summarize this video", "what is it about", "pull the captions", "key points of the lecture".',
  '자기소개서·지원서 문항에 「공백 포함 700자」「1,000바이트 이내」 같은 글자 수 기준이 있을 때. 쓴 답이 기준 안에 드는지 확인할 때.':
    'When a cover letter or application question has a length limit such as "700 characters with spaces" or "within 1,000 bytes", and the answer must be checked against it.',
  '과장님·팀장님께 올릴 1쪽 요약 보고, 점검 결과·메일 스레드를 개조식으로 정리할 때.':
    'For a one-page summary report to a manager, or to condense check results or an email thread into terse bullet style.',
  '규정·업무 안내처럼 자료 밖의 질문을 담당 부서로 보내야 하는 대화 에이전트. 서버가 답을 다 쓴 뒤 돌린다.':
    'For conversation agents, such as regulation or work guides, that must send questions outside their material to the responsible department. The server runs it after the answer is written.',
  '행사 결과·수상·협약·신규 서비스를 언론에 알릴 때, 내부 보고 자료를 보도자료 형식으로 바꿀 때, 정해진 분량(A4 1장, 800자 등)의 보도자료가 필요할 때.':
    'When announcing an event result, award, agreement or new service to the press, turning an internal report into a press release, or needing a release of a set length (one A4 page, 800 characters and so on).',
  '자료로 문서나 분석을 쓰기 전에, 자료가 답하지 않는 필수 사실이 있는지 볼 때. 「빠진 정보 먼저 묻기」를 켠 에이전트에서는 서버가 쓰기 전에 자동으로 돌린다.':
    'Before writing a document or analysis from material, to see whether required facts are left unanswered by it. In agents with "Ask for missing information first" on, the server runs it automatically before writing.',
  '규정·업무 안내처럼 자료에 있는 것만 답해야 하는 대화 에이전트. 서버가 답을 다 쓴 뒤 돌린다.':
    'For conversation agents, such as regulation or work guides, that may answer only what the material contains. The server runs it after the answer is written.',
  '써 둔 광고 문구·SNS 글·랜딩 카피가 채널 한도 안인지, 표시광고법상 위험한 표현이나 자료에 없는 수치가 있는지 확인할 때. 광고·홍보 문구를 쓰는 스킬이 마지막에 부른다.':
    'To check that ad copy, social posts or landing copy fit the channel limit and contain no expressions risky under advertising law or figures absent from the material. Skills that write ad or promotional copy call it at the end.',
  '회의 메모·전사본으로 정식 회의록을 만들 때, 누가 언제까지 무엇을 하기로 했는지 남겨야 할 때.':
    'When turning meeting notes or a transcript into formal minutes, and who agreed to do what by when must be recorded.',
  '규정·지침 파일을 자료로 둔 에이전트나 대화에서 「휴학은 언제까지」「졸업 학점」처럼 규정을 묻는 질문에 답할 때. 공개 챗봇의 지침으로 씁니다.':
    'For answering rule questions such as "until when can I take leave" or "graduation credits" in an agent or conversation that has regulation or guideline files as material. Used as the instructions of a public chatbot.',
  '메일·공지·보고서·논문 초록·계약 조항을 다른 언어로 옮길 때, 용어를 통일해 번역해야 할 때, 원문 형식(표·목록)을 살린 번역이 필요할 때.':
    'When translating an email, notice, report, paper abstract or contract clause into another language, keeping terms consistent, or preserving the source layout (tables, lists).',
  '회의록·일정·기한에 상대 날짜가 있을 때, 경력·사업 기간의 개월 수와 총합을 셀 때, 발주일에서 납기·영업일을 계산할 때.':
    'When minutes, schedules or deadlines contain relative dates, when counting months and totals for career or project periods, or when working out a delivery date or business days from an order date.',
  '자료로 발표 슬라이드(PPTX로 내려받기)를 만들 때, 결정권자 보고용·팀 공유용 발표를 구성할 때.':
    'When building presentation slides from material (downloadable as PPTX), for a decision-maker briefing or a team share.',
  '정해진 서식이 없는 기관 문서(행사 계획서, 결과 보고, 기안, 안내문)의 초안이 필요할 때.':
    'When you need a draft of an organizational document with no set form (event plan, result report, internal proposal, notice).',
  '표의 숫자를 그래프로 보고 싶을 때, 항목 비교·월별 추이를 차트로 만들 때, 보고에 넣을 차트가 필요할 때.':
    'When you want to see table figures as a graph, chart an item comparison or monthly trend, or need a chart for a report.',
  '여러 사람이 읽는 답(공개 챗봇, 공지, 안내문, 회람)에 개인정보가 섞여 나가지 않게 할 때. 에이전트나 묶음에 붙이면 서버가 다 쓴 뒤에 가립니다.':
    'To keep personal data out of answers many people read (public chatbot, notices, circulars). Attached to an agent or bundle, the server masks it after the answer is written.',
}

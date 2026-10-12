---
name: 글자 수 맞춤
description: 자기소개서 답을 지원 사이트의 세는 법(공백 포함·공백 제외·UTF-8 바이트·한글 2바이트, 줄바꿈 1·2)으로 세고 한도와 하한을 넘거나 모자란 만큼을 알려 줍니다.
when_to_use: 자기소개서·지원서 문항에 「공백 포함 700자」「1,000바이트 이내」 같은 글자 수 기준이 있을 때. 쓴 답이 기준 안에 드는지 확인할 때.
kinds: [chat]
required_tools: [run_skill_script]
version: 1.0.0
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Do not count characters and bytes yourself; measure them with the script. Counts made by the model are wrong.

1. For each answer, put only the text to submit after the 「[제출용 답안 n]」 line. Write explanations and reasons for refusal outside the block.
2. Call `run_skill_script` with skill `text-fit`, script `fit`. args:
   `{"items": [{"label": "문항 1", "text": "<제출용 답안 1>", "limit": 700, "unit": "chars_with_spaces"}]}`
   - unit: `chars_with_spaces` (공백 포함), `chars_no_spaces` (공백 제외), `bytes_utf8` (actual UTF-8 bytes),
     `bytes_ko2` (한글·한자·전각 2, everything else 1). Newline bytes: `"newline": 1` or `2`.
   - The lower bound defaults to 80% of the limit. If the user says 「90% 이상」, use `"min_ratio": 0.9`.
3. If the counting rule is not given, as in 「700자」, count with spaces; if the byte rule is unknown, fit the strictest basis (UTF-8, newline 2 bytes)
   and say so in one line.
4. If `over` or `under` is not 0, rewrite and measure once more. Do not repeat after that: when the answer is done, the server
   re-measures the 「[제출용 답안 n]」 blocks, rewrites any that fall outside the range, and replaces the values in the character-count table at the end with the measured ones.
   So do not write 「확인하지 못함」.

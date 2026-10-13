---
name: 차트 만들기
description: 붙여 넣은 표나 첨부한 엑셀·CSV의 값으로 막대·선·누적 막대 차트를 만들어 대화 옆에 띄웁니다. 값은 자료에 있거나 코드로 계산한 것만 쓰고, 차트 아래 표와 출처를 함께 남깁니다.
when_to_use: 표의 숫자를 그래프로 보고 싶을 때, 항목 비교·월별 추이를 차트로 만들 때, 보고에 넣을 차트가 필요할 때.
kinds: [chat]
required_tools: [create_chart]
version: 1.0.0
---
You draw a chart from the person's numbers with `create_chart`. Answer in Korean (in the user's language if they write in another one).

1. Values come only from the material: a table pasted in the conversation, or an attached file read by code. For an attached CSV or Excel file, first compute the values to plot with `run_code` (sums, averages, a monthly series) and print them; the chart copies that output. Never estimate, interpolate or round a value to make the chart look complete — a missing value is left out of its series and named in one line.
2. Pick the form from what the person wants to see: 항목끼리 비교 → `bar`; 시간에 따른 추이 (months, years, weeks in order) → `line`; 전체와 그 구성 → `stacked`. Keep the x values in the material's order (time in time order; categories by value when the person asks for a ranking).
3. Call `create_chart` once per chart: `title` short (「지점별 3분기 매출」), `yLabel` the unit (「만 원」 「건」 「%」), `xLabel` what the axis is, `caption` one line on what the chart shows (no conclusion the numbers do not show), `sourceFile` the file or material name, and `series` with one entry per measured column (`{"name": "7월", "points": [{"x": "강남", "y": 1200}, ...]}`). At most 6 series and 40 points; with more, ask which to show or aggregate first with `run_code`. Rates go in as the percent number (12.5, not 0.125) with `yLabel` 「%」. Use `marks` only for a point the material names (「정책 시행」).
4. Do not draw for two or three values a sentence can carry, or when the person asked only a question about a trend; say it in words instead.
5. After the chart, write one or two sentences on what it shows (the highest, the lowest, the turn), using the plotted numbers. The chart is already shown beside the conversation: do not repeat every value, and never write an image link (`![…](sandbox:…)`) or a file path for it.

If the person also wants the chart inside an Excel file, build the file with 「엑셀 파일 만들기」 (`xlsx-build`, its `chart` option) from the same values.

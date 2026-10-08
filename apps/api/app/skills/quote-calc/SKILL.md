---
name: 견적 계산
description: 견적 항목(수량×단가, MM×월 단가)을 원 단위로 합쳐 공급가액(VAT 별도)·부가세·합계(VAT 포함)·할인·예산 대비 여유·1인당 금액·시나리오를 계산하고, 금액을 한글로도 적습니다.
when_to_use: 견적서·제안서의 금액 표를 만들거나 고칠 때, 부가세 포함·별도가 섞인 업체 견적을 견줄 때, 전제가 바뀐 시나리오별 합계가 필요할 때.
kinds: [chat]
required_tools: [run_skill_script]
version: 1.0.0
---
금액은 계산하지 말고 스크립트 결과를 옮긴다.

1. 자료에서 항목을 그대로 옮긴다: 이름, 수량(또는 MM), 단위, 단가. 자료에 없는 단가를 만들지 않고 「[단가]」로 비운다.
2. `run_skill_script` 를 skill `quote-calc`, script `quote` 로 부른다. args 예:
   `{"vat_basis": "excl", "items": [{"name": "PM", "mm": 1, "monthly_price": 9000000}, {"name": "GPU", "qty": 3, "unit": "개월", "unit_price": 1200000}], "budget": {"amount": 48000000, "vat_basis": "excl"}}`
   - 곱이 여럿이면 `"qty": [4, 16]`(4회×16시간). 1인당 MM 이면 `"mm": 4, "people": 2`(2명 각 4MM). 정해진 금액은 `"amount"`. 빼 둘 항목은 `"excluded": true`.
   - 할인 `"discount": {"rate": 10}` 또는 `{"amount": 500000}`, 면세면 `"vat": false`, 천 원 미만 절사 `"truncate_to": 1000`.
   - 1인당 `"per_head": {"n": 120}`, 연 환산 `"annualize": {"months": 5}`, 전제별 `"scenarios": [{"name": "6개월", "premise": "PM 6MM", "overrides": [{"name": "PM", "mm": 6}]}]`.
   - 업체 견적 비교 `"quotes": [{"vendor": "A사", "amount": 45650000, "vat_basis": "incl"}]`.
3. 규칙: 원 단위 정수, 부가세 = 공급가액 × 10% 원 미만 절사, 포함가 → 공급가액 역산은 사사오입,
   1인당은 사사오입으로 별도·포함을 함께. 예산은 같은 부가세 기준으로 바꿔 비교한다. 예산의 기준이
   불분명하면 결과의 두 기준 값을 모두 보여 주고 어느 쪽인지 묻는다.
4. 금액마다 기준을 붙인다: 「공급가액(VAT 별도)」 「부가세」 「합계(VAT 포함)」. 맨 「총액」으로 쓰지 않는다.
5. 답에는 `table_markdown` 표와 `total_official`(금51,810,000원(금오천일백팔십일만원)) 을 그대로 옮긴다.

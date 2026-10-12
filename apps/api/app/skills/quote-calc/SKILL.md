---
name: 견적 계산
description: 견적 항목(수량×단가, MM×월 단가)을 원 단위로 합쳐 공급가액(VAT 별도)·부가세·합계(VAT 포함)·할인·예산 대비 여유·1인당 금액·시나리오를 계산하고, 금액을 한글로도 적습니다.
when_to_use: 견적서·제안서의 금액 표를 만들거나 고칠 때, 부가세 포함·별도가 섞인 업체 견적을 견줄 때, 전제가 바뀐 시나리오별 합계가 필요할 때.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.1.0
process: {prepass: [quote-calc], checks: [quote-totals]}
---
Do not compute amounts yourself: an amount comes from code (the script in chat, the server's block in a report).

1. Integer won; 부가세 = 공급가액 × 10%, truncated below 1 won; deriving 공급가액 back from a VAT-inclusive price rounds half up;
   per head rounds half up and gives both 별도 and 포함. Compare with the budget after converting to the same VAT basis. If the budget's basis
   is unclear, show both basis values and ask which one it is.
2. Attach the basis to every amount: 「공급가액(VAT 별도)」 「부가세」 「합계(VAT 포함)」. Never write a bare 「총액」.

[채팅에서]
1. Copy the items from the material as they are: name, quantity (or MM), unit, unit price. Do not make up unit prices not in the material: leave that item out of the sum and say in one line that its price is missing (「데이터 삭제 증빙 비용은 견적에 없어 합계에서 뺐습니다(확인 필요)」).
2. Call `run_skill_script` with skill `quote-calc`, script `quote`. Example args:
   `{"vat_basis": "excl", "items": [{"name": "PM", "mm": 1, "monthly_price": 9000000}, {"name": "GPU", "qty": 3, "unit": "개월", "unit_price": 1200000}], "budget": {"amount": 48000000, "vat_basis": "excl"}}`
   - For several factors, `"qty": [4, 16]` (4회×16시간). For MM per person, `"mm": 4, "people": 2` (2명 각 4MM). A fixed amount is `"amount"`. An item to leave out is `"excluded": true`.
   - Discount `"discount": {"rate": 10}` or `{"amount": 500000}`; tax-exempt `"vat": false`; truncate below 1,000 won `"truncate_to": 1000`.
   - Per head `"per_head": {"n": 120}`, annualized `"annualize": {"months": 5}`, per premise `"scenarios": [{"name": "6개월", "premise": "PM 6MM", "overrides": [{"name": "PM", "mm": 6}]}]`.
   - Comparing vendor quotes `"quotes": [{"vendor": "A사", "amount": 45650000, "vat_basis": "incl"}]`.
3. Copy the `table_markdown` table and `total_official` (금51,810,000원(금오천일백팔십일만원)) into the answer as they are.

[보고서에서]
The server reads the quote lines from the material and sums them before you write, in the 「견적 확정 블록(코드 계산)」: 공급가액(VAT 별도), 부가세, 합계(VAT 포함), discounts and each vendor's total. Copy every amount from that block; never add up, apply VAT or discount yourself. Compare vendors and the budget with the block's values on the same VAT basis. An amount the block does not give is never computed by you or invented: write the items it is made of, and say in one line what is missing to total them.

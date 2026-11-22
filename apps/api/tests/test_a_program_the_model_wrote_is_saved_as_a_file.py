"""A program or page the person asked for is an artifact even when the model never called
`create_artifact`: the answer's block, or the code a run proved, is kept; snippets stay chat."""

from __future__ import annotations

import inspect

import pytest

from app.routers import sessions
from app.services import code_file_save, sandbox
from app.services.tools import sandbox_tools
from app.services.tools.base import ToolContext

SEARCH_REQUEST = (
    "이진 탐색이랑 선형 탐색 파이썬으로 구현해줘. 함수 이름은 binary_search(arr, target), "
    "linear_search(arr, target) 이고 못 찾으면 -1 반환. unittest 테스트도 같이 넣어줘."
)
CSV_REQUEST = (
    "판매 내역 CSV를 읽어서 상품별 총매출이랑 월별 합계 출력하는 파이썬 스크립트 짜줘. "
    "샘플 CSV 내용은 코드 안 문자열로 넣어서 바로 돌려볼 수 있게. "
    "컬럼은 날짜, 상품, 수량, 단가. pandas 말고 csv 모듈로."
)

SEARCH_PY = '''import unittest


def linear_search(arr, target):
    """Index of the first match, or -1."""
    for index, value in enumerate(arr):
        if value == target:
            return index
    return -1


def binary_search(arr, target):
    """Index of a match in a sorted list, or -1."""
    low, high = 0, len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        if arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1


class SearchTests(unittest.TestCase):
    def test_found(self):
        self.assertEqual(binary_search([1, 3, 5, 7], 5), 2)
        self.assertEqual(linear_search([4, 2, 9], 9), 2)

    def test_missing(self):
        self.assertEqual(binary_search([], 1), -1)
        self.assertEqual(linear_search([1, 2], 3), -1)


if __name__ == "__main__":
    unittest.main()
'''

CSV_PY = '''import csv
import io
from collections import defaultdict

SAMPLE_CSV = """날짜,상품,수량,단가
2026-01-05,노트북,2,1200000
2026-01-18,마우스,10,25000
2026-02-02,노트북,1,1200000
2026-02-14,키보드,5,89000
"""

by_product = defaultdict(int)
by_month = defaultdict(int)
for row in csv.DictReader(io.StringIO(SAMPLE_CSV)):
    amount = int(row["수량"]) * int(row["단가"])
    by_product[row["상품"]] += amount
    by_month[row["날짜"][:7]] += amount

print("== 상품별 총매출 ==")
for name in sorted(by_product):
    print(f"{name}: {by_product[name]:,}원")
print("== 월별 합계 ==")
for month in sorted(by_month):
    print(f"{month}: {by_month[month]:,}원")
print("검산:", sum(by_product.values()) == sum(by_month.values()))
'''


def chat(code: str, language: str = "python", before: str = "", after: str = "") -> str:
    return f"{before}\n\n```{language}\n{code}```\n\n{after}".strip()


def test_a_script_left_in_the_chat_becomes_the_file_it_should_have_been():
    answer = chat(
        SEARCH_PY, before="두 함수와 테스트를 한 파일에 담았습니다.",
        after="`search.py` 로 저장해 돌리세요.")
    owed = code_file_save.file_owed(SEARCH_REQUEST, answer, [])
    assert owed is not None
    assert (owed.kind, owed.language, owed.title) == ("code", "python", "search.py")
    assert owed.content == SEARCH_PY.rstrip("\n")
    assert owed.request()["data"] == {"kind": "code", "content": owed.content, "language": "python"}


def test_a_file_without_a_named_file_is_titled_from_its_first_definition():
    owed = code_file_save.file_owed(SEARCH_REQUEST, chat(SEARCH_PY), [])
    assert owed is not None and owed.title == "linear_search.py"


def test_the_title_names_the_program_not_its_helpers_or_tests():
    library = (
        "class LibraryError(Exception):\n    pass\n\n\nclass _Shelf:\n    pass\n\n\n"
        "class Library:\n    def borrow(self, title):\n        return title\n"
    ) + "\n".join(f"x{n} = {n}" for n in range(10)) + "\n\nif __name__ == '__main__':\n    pass\n"
    owed = code_file_save.file_owed("도서관 클래스 만들어줘", chat(library), [])
    assert owed is not None and owed.title == "library.py"
    both = "프로그램은 `dijkstra.py`, 테스트는 `test_dijkstra.py` 에 둡니다."
    owed = code_file_save.file_owed(SEARCH_REQUEST, chat(SEARCH_PY, before=both), [])
    assert owed is not None and owed.title == "dijkstra.py"


def test_a_program_run_then_described_is_recovered_from_the_run():
    """A script run then described as put beside the chat is recovered from the run and saved."""
    answer = (
        "스크립트를 아티팩트로 만들어 두었습니다. 위에서 실제로 돌려 본 결과는 노트북 2,400,000원, "
        "마우스 250,000원입니다.\n\n```\n== 상품별 총매출 ==\n노트북: 2,400,000원\n```"
    )
    owed = code_file_save.file_owed(CSV_REQUEST, answer, [CSV_PY])
    assert owed is not None
    assert owed.content == CSV_PY.rstrip("\n") or owed.content == CSV_PY
    assert owed.title == "script.py"


def test_the_block_the_person_can_read_wins_over_a_run():
    owed = code_file_save.file_owed(SEARCH_REQUEST, chat(SEARCH_PY), [CSV_PY])
    assert owed is not None and "binary_search" in owed.content


def test_the_largest_block_is_the_file():
    usage = "from search import binary_search\nprint(binary_search([1, 2, 3], 2))\n"
    answer = chat(usage) + "\n\n" + chat(SEARCH_PY)
    owed = code_file_save.file_owed(SEARCH_REQUEST, answer, [])
    assert owed is not None and "class SearchTests" in owed.content


@pytest.mark.parametrize(
    "request_text",
    [
        "파이썬에서 리스트 정렬은 어떻게 해?",
        "클로저가 뭔지 예시 코드로 설명해줘",
        "이 함수가 왜 느린지 알려줘",
    ],
)
def test_a_question_is_answered_in_the_chat(request_text):
    assert code_file_save.file_owed(request_text, chat(SEARCH_PY), [CSV_PY]) is None


def test_a_snippet_stays_in_the_chat():
    snippet = (
        "def is_prime(n):\n"
        '    """True when n is prime."""\n'
        "    if n < 2:\n"
        "        return False\n"
        "    divisor = 2\n"
        "    while divisor * divisor <= n:\n"
        "        if n % divisor == 0:\n"
        "            return False\n"
        "        divisor += 1\n"
        "    return True\n"
    )
    assert code_file_save.code_lines(snippet) >= code_file_save.MIN_CODE_LINES
    assert code_file_save.file_owed("소수 판별 함수 만들어줘", chat(snippet), []) is None
    assert code_file_save.file_owed("소수 판별 스크립트 만들어줘", chat(snippet), []) is None
    # the same function grown into a script that lists the primes, asked for as one
    script = snippet + "\nprimes = []\nfor number in range(2, 50):\n    if is_prime(number):\n" \
        "        primes.append(number)\nprint(primes)\n"
    assert code_file_save.file_owed("소수 판별 함수 만들어줘", chat(script), []) is None
    assert code_file_save.file_owed("소수 판별 스크립트 만들어줘", chat(script), []) is not None
    short = "def add(a, b):\n    return a + b\n\nprint(add(1, 2))\n"
    assert code_file_save.file_owed("덧셈 스크립트 만들어줘", chat(short), []) is None


def test_code_the_person_pasted_is_theirs_to_fix_not_a_new_file():
    pasted = f"이 코드로 테스트 만들어줘\n\n```python\n{SEARCH_PY}```"
    assert code_file_save.file_owed(pasted, chat(SEARCH_PY), []) is None


def test_output_and_shell_commands_are_not_files():
    answer = (
        "실행하면 이렇게 나옵니다.\n\n```\n"
        + "\n".join(f"line {n}" for n in range(30))
        + "\n```\n\n"
        "```bash\n" + "\n".join(f"pip install package{n}" for n in range(12)) + "\n```"
    )
    assert code_file_save.file_owed(CSV_REQUEST, answer, []) is None


def test_a_page_is_kept_but_a_fragment_is_not():
    page = (
        "<!doctype html>\n<html lang=\"ko\">\n<head><meta charset=\"utf-8\">\n"
        "<title>팁 계산기</title>\n<style>body{font-family:sans-serif}</style></head>\n"
        "<body><input id=\"bill\" type=\"number\"><button id=\"go\" type=\"button\">계산</button>\n"
        "<p id=\"out\"></p>\n<script>document.getElementById('go').onclick=()=>{"
        "out.textContent=bill.value*1.1}</script></body></html>\n"
    )
    owed = code_file_save.file_owed("팁 계산기 웹페이지 만들어줘", chat(page, "html"), [])
    assert owed is not None
    assert (owed.kind, owed.language, owed.title) == ("html", "html", "팁 계산기.html")
    fragment = '<button class="primary">저장</button>'
    assert code_file_save.file_owed("버튼 만들어줘", chat(fragment + "\n", "html"), []) is None


def test_the_answer_is_not_rewritten():
    answer = chat(SEARCH_PY, before="아래 코드입니다.")
    code_file_save.file_owed(SEARCH_REQUEST, answer, [])
    assert answer.startswith("아래 코드입니다.")


@pytest.mark.asyncio
async def test_a_run_that_worked_is_remembered_and_a_failed_one_is_not(monkeypatch):
    class Sandbox:
        pass

    async def client():
        return Sandbox()

    async def no_files(user_id, session_id):
        return []

    async def run(client, state, code, lang="py", refs=None):
        crashed = "raise" in code
        return sandbox.RunResult(
            stdout="" if crashed else "ok\n", stderr="Traceback" if crashed else "", session_id="x"
        )

    async def collect(client, result, **kwargs):
        return [], []

    monkeypatch.setattr(sandbox, "client", client)
    monkeypatch.setattr(sandbox, "session_files", no_files)
    monkeypatch.setattr(sandbox, "run", run)
    monkeypatch.setattr(sandbox, "collect_outputs", collect)
    ctx = ToolContext(user_id="u", session_id="s")
    await sandbox_tools.execute_code({"code": "raise SystemExit(1)"}, ctx)
    await sandbox_tools.execute_code({"code": SEARCH_PY}, ctx)
    assert ctx.code_runs == [SEARCH_PY]


def test_the_turn_saves_the_owed_file_before_it_stores_the_artifacts():
    source = inspect.getsource(sessions._answer_turn)
    saved = source.find("code_file_save.file_owed(")
    assert 0 < saved < source.find("new_artifact = await _store_artifacts(")
    # only when the model made nothing itself
    assert "not ctx.pending_artifacts" in source

"""Detect a requested program or page the model wrote but never saved as an artifact.

Covers a program left in a fenced block without `create_artifact`, or run via `execute_code` and
then described in prose. After the turn, a file is owed only when all hold:

1. the user asked to make something (creation verb) and pasted no code of their own;
2. the text is file-shaped: a full HTML document, or code in a known language of at least
   `MIN_CODE_LINES` lines with an entry point, tests or several top-level definitions, or at least
   `MIN_NAMED_LINES` lines when the request itself names code, script, program, file, class or app;
3. the turn made no artifact, document or chart.

The source is the largest such fenced block of the answer, else the largest program a successful
`execute_code` run used this turn. The answer text is never rewritten."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Fewest lines of code (blank and comment-only lines not counted) for a block to be a file.
MIN_CODE_LINES = 10
#: Fewest when nothing but the request's own words (「스크립트 만들어줘」) says it is a file.
MIN_NAMED_LINES = 12

#: A request that asks for something to be made, in Korean and English.
_MAKES = re.compile(
    r"만들|짜\s*(?:줘|주|봐|라)|작성|구현|설계|개발|코딩|제작|"
    r"써\s*(?:줘|주|봐)|풀어\s*(?:줘|주|봐)|"
    r"\b(?:write|implement|create|build|make|develop|generate|solve|code\s+up)\b",
    re.I,
)

#: A request that names what it wants as a file: a script, a program, a class, an app.
_NAMES_A_FILE = re.compile(
    r"코드|스크립트|프로그램|파일|클래스|모듈|앱|애플리케이션|서버|봇|도구|툴|CLI|"
    r"\b(?:code|script|program|file|class|module|app|application|server|bot|tool|cli)\b",
    re.I,
)

_FENCE = re.compile(
    r"^[ \t]{0,3}```[ \t]*([\w#+.-]*)[^\n]*\n(.*?)^[ \t]{0,3}```[ \t]*$", re.M | re.S)

#: Fence tags that are programming languages, by the language name an artifact stores.
_LANGUAGES = {
    "python": "python", "py": "python", "python3": "python",
    "javascript": "javascript", "js": "javascript", "typescript": "typescript", "ts": "typescript",
    "java": "java", "go": "go", "golang": "go", "rust": "rust", "rs": "rust",
    "c": "c", "cpp": "cpp", "c++": "cpp", "csharp": "csharp", "cs": "csharp", "c#": "csharp",
    "kotlin": "kotlin", "swift": "swift", "ruby": "ruby", "rb": "ruby", "php": "php",
    "bash": "bash", "sh": "bash", "shell": "bash",
}
_EXTENSIONS = {
    "python": "py", "javascript": "js", "typescript": "ts", "java": "java", "go": "go",
    "rust": "rs", "c": "c", "cpp": "cpp", "csharp": "cs", "kotlin": "kt", "swift": "swift",
    "ruby": "rb", "php": "php", "bash": "sh",
}

#: A program that is run, tested or split into parts, rather than a function shown to explain.
_ENTRY_POINT = re.compile(
    r"if\s+__name__\s*==|\bunittest\b|\bpytest\b|^\s*def\s+test_|\bmain\s*\(|"
    r"public\s+static\s+void\s+main|^\s*func\s+main\b|^\s*fn\s+main\b|#\[test\]|"
    r"\bdescribe\s*\(|\bit\s*\(|\bassert\w*\s*\(|^#!",
    re.M,
)
_TOP_LEVEL_DEFINITION = re.compile(
    r"^(?:async\s+def|def|class|function|public\s+class|func|fn|export\s+(?:default\s+)?"
    r"(?:async\s+)?(?:function|class))\b",
    re.M,
)
_HTML_DOCUMENT = re.compile(r"<!doctype\s+html|<html[\s>]", re.I)
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_FILE_NAME = r"([A-Za-z_][\w-]*)\.({exts})\b"
#: Names no file is called: placeholders in the answer's prose, entry points.
_GENERIC = frozenset({"main", "script", "file", "your_file", "filename", "example", "test", "app"})
_DEFINITION_NAME = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", re.M)


@dataclass(frozen=True, slots=True)
class CodeFile:
    """What to store: an artifact request, the shape `create_artifact` queues."""

    kind: str
    title: str
    language: str
    content: str

    def request(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title[:200],
            "data": {"kind": self.kind, "content": self.content, "language": self.language},
        }


def asks_for_a_program(request: str) -> bool:
    """The person asked to make something, with no code of their own to work on."""
    text = request or ""
    return bool(_MAKES.search(text)) and "```" not in text


def code_lines(code: str) -> int:
    """Lines that carry code: blank lines and comment-only lines do not count."""
    return sum(
        1 for line in code.splitlines()
        if line.strip() and not line.strip().startswith(("#", "//", "/*", "*", "--"))
    )


def _is_file_shaped(language: str, code: str, request: str) -> bool:
    if language == "html":
        return bool(_HTML_DOCUMENT.search(code)) and len(code.strip()) >= 200
    if code_lines(code) < MIN_CODE_LINES:
        return False
    if language == "bash" and not code.lstrip().startswith("#!"):
        # shell lines in an answer are commands to type, not a script
        return False
    if _ENTRY_POINT.search(code) or len(_TOP_LEVEL_DEFINITION.findall(code)) >= 2:
        return True
    return code_lines(code) >= MIN_NAMED_LINES and bool(_NAMES_A_FILE.search(request))


def fenced_blocks(answer: str) -> list[tuple[str, str]]:
    """(language, code) of each closed fenced block that names a language."""
    found = []
    for match in _FENCE.finditer(answer or ""):
        tag = match.group(1).lower()
        language = "html" if tag in ("html", "htm") else _LANGUAGES.get(tag, "")
        if language:
            found.append((language, match.group(2).rstrip("\n")))
    return found


def _title(language: str, code: str, answer: str) -> str:
    if language == "html":
        named = _HTML_TITLE.search(code)
        text = " ".join(named.group(1).split()) if named else ""
        return (text[:60] or "page") + ".html"
    extension = _EXTENSIONS[language]
    named = [
        m.group(1) for m in re.finditer(_FILE_NAME.format(exts=re.escape(extension)), answer or "")
        if m.group(1).lower() not in _GENERIC
    ]
    # a file the answer names, the program itself rather than its test file when it names both
    named.sort(key=lambda stem: stem.lower().startswith("test") or stem.lower().endswith("test"))
    if named:
        return f"{named[0]}.{extension}"
    defined = [
        name for name in _DEFINITION_NAME.findall(code)
        if name not in _GENERIC and not name.startswith("_")
        and not name.endswith(("Error", "Exception")) and not name.lower().startswith("test")
    ]
    stem = re.sub(r"(?<!^)(?=[A-Z])", "_", defined[0]).lower() if defined else "script"
    return f"{stem}.{extension}"


def _largest(candidates: list[tuple[str, str]], request: str) -> tuple[str, str] | None:
    shaped = [c for c in candidates if _is_file_shaped(c[0], c[1], request)]
    return max(shaped, key=lambda c: len(c[1])) if shaped else None


def file_owed(request: str, answer: str, code_runs: list[str]) -> CodeFile | None:
    """The file this turn owed the person, or None when nothing in it was one.

    `code_runs` are the programs `execute_code` ran without failing this turn.
    """
    if not asks_for_a_program(request):
        return None
    pick = _largest(fenced_blocks(answer), request)
    if pick is None:
        pick = _largest([("python", code) for code in code_runs], request)
    if pick is None:
        return None
    language, code = pick
    return CodeFile(
        kind="html" if language == "html" else "code",
        title=_title(language, code, answer),
        language=language,
        content=code,
    )

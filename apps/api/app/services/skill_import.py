"""Uploaded skills: a SKILL.md, or a .zip/.skill package around one, read into a skill row.

Nothing here touches the file system. An archive is read in memory, entry by entry and
within fixed limits; only text the person can read in the skill page is kept.

- The server fields (`stage`, `entry`, `post_check`, `post_judge`, `copy_check`,
  `copy_kinds`, `tables`, `own_stage`, `uses`) are kept when they name shipped scripts
  (`services/skill_specs.py`); anything else is a finding and is dropped.
- Python scripts under `scripts/` are kept in the row (`Skill.script_files`), shown in the
  preview with their own scan, and run only in the code sandbox through `run_skill_script`
  — never imported by the server, never written to `app/skills`. Other script types are
  dropped as before. Sharing a skill with scripts waits for an administrator.

Every problem is a `Finding`: `block` stops the upload (structure or safety), `warn`
is shown and the upload may go on. The scans are heuristics, not a guarantee.
"""

from __future__ import annotations

import ast
import io
import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from app.models.chat import SessionKind
from app.schemas.workspace import SkillIn
from app.services import skill_packages, skill_specs

#: One uploaded file, raw.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
#: Uncompressed total of one archive.
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 50
#: Uncompressed/compressed past this is treated as a bomb.
MAX_RATIO = 100
#: Files per request.
MAX_FILES = 10

ARCHIVE_SUFFIXES = (".zip", ".skill")
TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".yaml", ".yml"})
SCRIPT_SUFFIXES = frozenset(
    {
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".rb",
        ".pl",
        ".php",
        ".ps1",
        ".bat",
        ".cmd",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".jar",
        ".class",
        ".wasm",
        ".pyc",
    }
)
NESTED_SUFFIXES = frozenset(
    {".zip", ".skill", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".whl", ".jar"}
)
#: Archive noise that is skipped without a word.
_JUNK = ("__MACOSX/", ".DS_Store", "Thumbs.db")

#: Front matter a person may set: the catalogue fields, and the server fields naming shipped
#: scripts (`skill_specs.FIELDS`, checked against its allowlists). `status` is the
#: catalogue's alone.
ACCEPTED = ("name", "description", "when_to_use", "kinds", "required_tools", "version",
            *skill_specs.FIELDS)
SERVER_ONLY = tuple(f for f in skill_packages._FIELDS if f not in ACCEPTED)
#: A person's own scripts: Python only (the sandbox runs `python3`), under `scripts/`.
KEPT_SCRIPT_SUFFIX = ".py"
MAX_SCRIPTS = 8
MAX_SCRIPT_BYTES = 64 * 1024
_SCRIPT_NAME = re.compile(r"[a-z0-9_]{1,40}")

BODY_MAX = next(
    m.max_length for m in SkillIn.model_fields["body"].metadata if getattr(m, "max_length", None)
)
NAME_MAX = next(
    m.max_length for m in SkillIn.model_fields["name"].metadata if getattr(m, "max_length", None)
)
TEXT_MAX = next(
    m.max_length
    for m in SkillIn.model_fields["description"].metadata
    if getattr(m, "max_length", None)
)
_KINDS = {k.value for k in SessionKind}
RESOURCES_HEADING = "## 참고 자료"


@dataclass(slots=True)
class Finding:
    severity: str  # "block" | "warn"
    code: str
    message: str
    file: str = ""
    line: int | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == "block"


@dataclass(slots=True)
class Excluded:
    path: str
    reason: str  # "script" | "binary" | "unused" | "too_long"


@dataclass(slots=True)
class Script:
    name: str
    source: str
    summary: str = ""

    @property
    def size(self) -> int:
        return len(self.source.encode("utf-8"))

    def stored(self) -> dict:
        return {"name": self.name, "source": self.source, "summary": self.summary,
                "size": self.size}


@dataclass(slots=True)
class Preview:
    filename: str
    name: str = ""
    description: str = ""
    when_to_use: str = ""
    kinds: list[str] = field(default_factory=lambda: ["chat"])
    required_tools: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    body: str = ""
    kept: list[str] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: What the server runs for it, as `Skill.server_fields` stores it; None for nothing.
    server: dict | None = None
    #: Its own Python scripts, kept for the sandbox.
    scripts: list[Script] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.blocking for f in self.findings)

    def block(self, code: str, message: str, file: str = "") -> Preview:
        self.findings.append(Finding("block", code, message, file))
        return self

    def warn(self, code: str, message: str, file: str = "", line: int | None = None) -> None:
        self.findings.append(Finding("warn", code, message, file, line))


# ── entry points ──────────────────────────────────────────────────────


def inspect(filename: str, data: bytes, known_tools: set[str] | None = None) -> Preview:
    """What `filename` would become as a skill, with every finding. Never raises on content."""
    name = PurePosixPath((filename or "SKILL.md").replace("\\", "/")).name[:200] or "SKILL.md"
    preview = Preview(filename=name)
    if len(data) > MAX_UPLOAD_BYTES:
        return preview.block("file_too_large", "파일이 2MB를 넘습니다.", name)
    lower = name.lower()
    if lower.endswith(ARCHIVE_SUFFIXES):
        _read_archive(preview, data)
    elif lower.endswith(".md"):
        text = _decode(data)
        if text is None:
            return preview.block("not_utf8", "UTF-8 텍스트가 아닙니다.", name)
        preview.kept.append(name)
        _read_skill_md(preview, text, name)
    else:
        return preview.block("unsupported_type", ".md, .zip, .skill 파일만 올릴 수 있습니다.", name)
    if preview.ok:
        _check_tools(preview, known_tools)
        preview.findings.extend(scan_text(_scannable(preview), file=name))
    return preview


def scan_script(source: str, *, file: str) -> list[Finding]:
    """A person's script read without running it: what it reaches outside the sandbox's
    working folder (network, other processes, files elsewhere, code built at run time) is a
    warning; a secret or a hidden direction control blocks, as on sharing."""
    out = [f for f in scan_text(source, file=file, sharing=True)
           if f.code in ("secret_like", "hidden_characters", "encoded_blob")]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [*out, Finding("block", "script_syntax",
                              "파이썬 문법 오류가 있어 실행할 수 없습니다.", file, exc.lineno)]
    seen: set[str] = set()

    def flag(code: str, message: str, line: int | None) -> None:
        if code not in seen:
            seen.add(code)
            out.append(Finding("warn", code, message, file, line))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [
                node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root in _NETWORK_MODULES:
                    flag("script_network", f"네트워크에 접속하는 모듈({root})을 씁니다. "
                         "샌드박스에서는 외부로 나가지 못할 수 있습니다.", node.lineno)
                elif root in _PROCESS_MODULES:
                    flag("script_process", f"다른 프로그램을 실행하는 모듈({root})을 씁니다.",
                         node.lineno)
        elif isinstance(node, ast.Call):
            called = _called(node.func)
            if called in _PROCESS_CALLS:
                flag("script_process", f"다른 프로그램을 실행합니다({called}).", node.lineno)
            elif called in _DYNAMIC_CALLS:
                flag("script_dynamic_code", f"실행 중에 만든 코드를 돌립니다({called}).",
                     node.lineno)
            elif _writes_outside(node, called):
                flag("script_file_write", "작업 폴더 밖의 경로에 쓰거나 지웁니다.", node.lineno)
    return out


_NETWORK_MODULES = frozenset({"socket", "urllib", "urllib2", "urllib3", "http", "requests",
                              "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib", "websocket",
                              "websockets", "paramiko", "ssl", "xmlrpc", "poplib", "imaplib"})
_PROCESS_MODULES = frozenset({"subprocess", "pty", "multiprocessing", "ctypes", "signal"})
_PROCESS_CALLS = frozenset({"os.system", "os.popen", "os.execv", "os.execve", "os.execl",
                            "os.execlp", "os.execvp", "os.spawnl", "os.spawnv", "os.fork",
                            "os.kill", "system", "popen"})
_DYNAMIC_CALLS = frozenset({"eval", "exec", "compile", "__import__", "importlib.import_module"})
_WRITE_CALLS = frozenset({"os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename",
                          "os.replace", "os.chmod", "os.chown", "shutil.rmtree", "shutil.move",
                          "shutil.copy", "shutil.copyfile", "shutil.copytree"})


def _called(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _outside(node: ast.expr | None) -> bool:
    """A literal path that leaves the working folder: absolute, home or `..`."""
    if isinstance(node, ast.Call) and _called(node.func) in ("Path", "pathlib.Path") \
            and node.args:
        node = node.args[0]
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    path = node.value.strip()
    return path.startswith(("/", "~", "\\")) or ".." in path.replace("\\", "/").split("/") \
        or bool(re.match(r"^[A-Za-z]:", path))


def _writes_outside(node: ast.Call, called: str) -> bool:
    if called == "open" and node.args:
        mode = node.args[1] if len(node.args) > 1 else next(
            (k.value for k in node.keywords if k.arg == "mode"), None)
        writing = isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
            and any(c in mode.value for c in "wax+")
        return writing and _outside(node.args[0])
    if called in _WRITE_CALLS:
        return any(_outside(arg) for arg in node.args)
    if isinstance(node.func, ast.Attribute) and node.func.attr in (
            "write_text", "write_bytes", "unlink", "rmdir", "mkdir", "touch"):
        return _outside(node.func.value)
    return False


def scan_text(text: str, *, file: str = "", sharing: bool = False) -> list[Finding]:
    """Heuristic warnings on instruction text. With `sharing`, secrets and hidden
    direction controls block: a shared skill spreads them to every copy."""
    out: list[Finding] = []
    lines = text.splitlines()[:5_000]
    hard = "block" if sharing else "warn"

    def first(pattern: re.Pattern[str]) -> int | None:
        for number, line in enumerate(lines, 1):
            if pattern.search(line[:4_000]):
                return number
        return None

    if (n := first(_OVERRIDE)) is not None:
        out.append(
            Finding(
                "warn",
                "instruction_override",
                "이전 지시를 무시하라는 식의 문장이 있습니다.",
                file,
                n,
            )
        )
    if (n := _exfil_line(lines)) is not None:
        out.append(
            Finding(
                "warn",
                "data_exfiltration",
                "자료를 외부 주소나 메일로 보내라는 지시가 있습니다.",
                file,
                n,
            )
        )
    if (n := _hidden_line(lines)) is not None:
        out.append(
            Finding(
                hard, "hidden_characters", "보이지 않는 방향 제어·폭 없는 문자가 있습니다.", file, n
            )
        )
    if (n := first(_BASE64)) is not None:
        out.append(Finding("warn", "encoded_blob", "긴 base64 인코딩 덩어리가 있습니다.", file, n))
    if (n := first(_SECRET)) is not None:
        # The value itself is never repeated: only where it is.
        out.append(
            Finding(hard, "secret_like", "API 키·토큰처럼 보이는 문자열이 있습니다.", file, n)
        )
    return out


def blocking_on_share(name: str, description: str, when_to_use: str, body: str) -> list[Finding]:
    """Block-level findings for a skill about to be shared with the organisation."""
    text = "\n".join((name, description, when_to_use, body))
    return [f for f in scan_text(text, sharing=True) if f.blocking]


def to_skill_md(
    *,
    name: str,
    description: str,
    when_to_use: str,
    kinds: list[str] | None,
    required_tools: list[str] | None,
    version: str,
    body: str,
    server: dict | None = None,
) -> str:
    """A skill row as a SKILL.md that `inspect` reads back to the same fields."""
    meta = {
        "name": name,
        "description": description,
        "when_to_use": when_to_use or description,
        "kinds": list(kinds or ["chat"]),
        "required_tools": list(required_tools or []),
        "version": version or "1.0.0",
    }
    # Only the server fields that ask for something, in SKILL.md's own order.
    for key, value in skill_specs.normalize(server).items():
        if value:
            meta[key] = value
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=10_000).strip()
    return f"---\n{head}\n---\n{body.strip()}\n"


# ── SKILL.md ──────────────────────────────────────────────────────────


def _decode(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text.replace("\r\n", "\n")


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _read_skill_md(preview: Preview, text: str, file: str) -> None:
    try:
        meta, body = skill_packages.split_front_matter(text.lstrip("\ufeff"))
    except (skill_packages.PackageError, yaml.YAMLError):
        preview.block(
            "front_matter", "맨 앞에 --- 로 감싼 YAML 머리(name, description)가 필요합니다.", file
        )
        return
    missing = [key for key in ("name", "description") if not _clean(meta.get(key))]
    if missing:
        preview.block("missing_fields", f"YAML 머리에 {', '.join(missing)} 가 없습니다.", file)
        return
    _read_server_fields(preview, meta, file)
    ignored = [key for key in meta if key in SERVER_ONLY]
    if ignored:
        preview.warn(
            "server_field_ignored",
            f"서버 전용 항목은 무시했어요: {', '.join(sorted(ignored))}",
            file,
        )
    unknown = [str(key)[:40] for key in meta if key not in skill_packages._FIELDS]
    if unknown:
        preview.warn(
            "unknown_field", f"모르는 항목은 무시했어요: {', '.join(sorted(unknown)[:10])}", file
        )

    preview.name = _clean(meta["name"])
    preview.description = _clean(meta["description"])
    preview.when_to_use = _clean(meta.get("when_to_use")) or preview.description
    if len(preview.name) > NAME_MAX:
        preview.block("too_long", f"name 은 {NAME_MAX}자까지입니다.", file)
    if max(len(preview.description), len(preview.when_to_use)) > TEXT_MAX:
        preview.block("too_long", f"description·when_to_use 는 {TEXT_MAX}자까지입니다.", file)

    kinds = meta.get("kinds")
    if kinds:
        items = (
            [kinds] if isinstance(kinds, str) else list(kinds) if isinstance(kinds, list) else []
        )
        good = [k for k in dict.fromkeys(str(i).strip() for i in items) if k in _KINDS]
        if len(good) != len(items):
            preview.warn("unknown_kind", "모르는 kinds 값은 뺐어요.", file)
        preview.kinds = good or ["chat"]

    tools = meta.get("required_tools") or []
    items = [tools] if isinstance(tools, str) else list(tools) if isinstance(tools, list) else []
    preview.required_tools = [t for t in dict.fromkeys(str(i).strip()[:120] for i in items) if t]

    version = _clean(meta.get("version"))
    preview.version = version[:40] if version else "1.0.0"

    body = body.strip()
    if not body:
        preview.block("empty_body", "SKILL.md 본문(절차)이 비어 있습니다.", file)
    elif len(body) > BODY_MAX:
        preview.block("body_too_long", f"본문이 {BODY_MAX:,}자를 넘습니다.", file)
    preview.body = body


def _read_server_fields(preview: Preview, meta: dict, file: str) -> None:
    """The server fields, kept where they name shipped scripts; each refusal a warning."""
    raw = {key: meta[key] for key in skill_specs.FIELDS if key in meta}
    if not raw:
        return
    fields, errors = skill_specs.validate(raw, own_scripts=tuple(s.name for s in preview.scripts))
    for error in errors:
        preview.warn(error.code, f"{error} 이 항목은 뺐어요.", file)
    preview.server = None if skill_specs.empty(fields) else fields


def _check_tools(preview: Preview, known: set[str] | None) -> None:
    keep: list[str] = []
    dropped: list[str] = []
    runs_scripts = bool(preview.scripts or (preview.server or {}).get("uses"))
    for tool in preview.required_tools:
        if tool == "run_skill_script" and runs_scripts:
            # It runs its own scripts (in the sandbox) or shipped ones it names.
            keep.append(tool)
        elif tool == "run_skill_script":
            preview.warn(
                "script_tool_dropped",
                "올린 스킬의 스크립트는 실행하지 않아 run_skill_script 를 뺐어요.",
            )
        elif known is not None and tool not in known:
            dropped.append(tool)
        else:
            keep.append(tool)
    if dropped:
        preview.warn("unknown_tools", f"모르는 도구는 뺐어요: {', '.join(dropped[:10])}")
    preview.required_tools = keep


def _scannable(preview: Preview) -> str:
    return "\n".join((preview.name, preview.description, preview.when_to_use, preview.body))


# ── archives ──────────────────────────────────────────────────────────


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _unsafe(path: str) -> bool:
    if not path or "\\" in path or "\x00" in path or path.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:", path):
        return True
    return any(part in ("..", ".") for part in path.rstrip("/").split("/"))


def _read_archive(preview: Preview, data: bytes) -> None:
    file = preview.filename
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        preview.block("bad_archive", "zip 파일을 열 수 없습니다.", file)
        return
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            preview.block("too_many_entries", f"압축 안 항목이 {MAX_ENTRIES}개를 넘습니다.", file)
            return
        total = 0
        entries: list[zipfile.ZipInfo] = []
        for info in infos:
            path = info.filename
            if _unsafe(path):
                preview.block(
                    "unsafe_path", "압축 안에 절대 경로나 .. 가 든 경로가 있습니다.", file
                )
                return
            if _is_symlink(info):
                preview.block("symlink", "압축 안에 심볼릭 링크가 있습니다.", file)
                return
            if info.flag_bits & 0x1:
                preview.block("encrypted", "암호가 걸린 압축은 읽지 않습니다.", file)
                return
            if info.is_dir() or any(j in path for j in _JUNK):
                continue
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                preview.block("too_large", "압축을 푼 크기가 2MB를 넘습니다.", file)
                return
            if info.file_size > 1024 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                preview.block(
                    "compression_ratio", "압축률이 비정상적으로 높습니다(압축 폭탄 의심).", file
                )
                return
            if PurePosixPath(path).suffix.lower() in NESTED_SUFFIXES:
                preview.block("nested_archive", "압축 안에 또 다른 압축 파일이 있습니다.", file)
                return
            entries.append(info)

        skill_mds = [i for i in entries if PurePosixPath(i.filename).name.lower() == "skill.md"]
        if not skill_mds:
            preview.block("no_skill_md", "압축 안에 SKILL.md 가 없습니다.", file)
            return
        if len(skill_mds) > 1:
            preview.block(
                "multiple_skill_md",
                "압축 안에 SKILL.md 가 여러 개입니다. 하나만 넣어 주세요.",
                file,
            )
            return
        root_parts = PurePosixPath(skill_mds[0].filename).parts[:-1]
        if len(root_parts) > 1:
            preview.block(
                "skill_md_not_at_root",
                "SKILL.md 는 압축 맨 위나 폴더 하나 바로 안에 있어야 합니다.",
                file,
            )
            return
        root = "/".join(root_parts)
        if root and any(not i.filename.startswith(root + "/") for i in entries):
            preview.block(
                "skill_md_not_at_root",
                "SKILL.md 가 든 폴더 밖에도 파일이 있습니다. 폴더 하나로 묶어 주세요.",
                file,
            )
            return

        skill_rel = PurePosixPath(skill_mds[0].filename).name
        read_total = 0
        texts: dict[str, str] = {}
        skill_text: str | None = None
        for info in entries:
            rel = info.filename[len(root) + 1 :] if root else info.filename
            try:
                with archive.open(info) as handle:
                    # Never trust the header: read at most what is left of the budget.
                    raw = handle.read(MAX_TOTAL_BYTES - read_total + 1)
            except (
                zipfile.BadZipFile,
                NotImplementedError,
                RuntimeError,
                EOFError,
                OSError,
                zlib.error,
            ):
                preview.block("bad_archive", "zip 파일을 열 수 없습니다.", file)
                return
            read_total += len(raw)
            if read_total > MAX_TOTAL_BYTES:
                preview.block("too_large", "압축을 푼 크기가 2MB를 넘습니다.", file)
                return
            if raw.startswith((b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!")):
                preview.block("nested_archive", "압축 안에 또 다른 압축 파일이 있습니다.", file)
                return
            if rel == skill_rel:
                skill_text = _decode(raw)
                continue
            suffix = PurePosixPath(rel).suffix.lower()
            if _own_script(rel) and not raw.startswith((b"\x7fELF", b"MZ")):
                if not _keep_script(preview, rel, raw):
                    return
                continue
            if suffix in SCRIPT_SUFFIXES or raw.startswith((b"#!", b"\x7fELF", b"MZ")):
                preview.excluded.append(Excluded(rel, "script"))
                continue
            text = _decode(raw) if suffix in TEXT_SUFFIXES else None
            if text is None:
                preview.excluded.append(Excluded(rel, "binary"))
                continue
            texts[rel] = text

        if any(e.reason == "script" for e in preview.excluded):
            preview.warn("scripts_excluded",
                         "파이썬이 아닌 스크립트와 scripts/ 밖의 스크립트는 제외했어요.", file)

    if skill_text is None:
        preview.block("not_utf8", "SKILL.md 가 UTF-8 텍스트가 아닙니다.", file)
        return
    preview.scripts.sort(key=lambda x: x.name)
    preview.kept.append(skill_rel)
    _read_skill_md(preview, skill_text, file)
    if not preview.ok:
        return
    _append_resources(preview, texts)


def _own_script(rel: str) -> bool:
    path = PurePosixPath(rel)
    return len(path.parts) == 2 and path.parts[0] == "scripts" \
        and path.suffix.lower() == KEPT_SCRIPT_SUFFIX


def _keep_script(preview: Preview, rel: str, raw: bytes) -> bool:
    """Keeps one `scripts/<name>.py` with its scan; False when it blocks the upload."""
    file = preview.filename
    stem = PurePosixPath(rel).stem
    if stem.startswith("_"):
        preview.excluded.append(Excluded(rel, "unused"))
        return True
    if not _SCRIPT_NAME.fullmatch(stem):
        preview.block("script_name", "스크립트 이름은 영문 소문자·숫자·_ 로 40자까지입니다.", rel)
        return False
    if len(preview.scripts) >= MAX_SCRIPTS:
        preview.block("too_many_scripts", f"스크립트는 {MAX_SCRIPTS}개까지 올릴 수 있습니다.", file)
        return False
    if len(raw) > MAX_SCRIPT_BYTES:
        preview.block("script_too_large",
                      f"스크립트 하나는 {MAX_SCRIPT_BYTES // 1024}KB까지입니다.", rel)
        return False
    source = _decode(raw)
    if source is None:
        preview.block("not_utf8", "스크립트가 UTF-8 텍스트가 아닙니다.", rel)
        return False
    findings = scan_script(source, file=rel)
    preview.findings.extend(findings)
    summary = ""
    if not any(f.code == "script_syntax" for f in findings):
        doc = ast.get_docstring(ast.parse(source)) or ""
        summary = doc.strip().splitlines()[0][:200] if doc.strip() else ""
    preview.scripts.append(Script(name=stem, source=source, summary=summary))
    preview.kept.append(rel)
    return True


def _append_resources(preview: Preview, texts: dict[str, str]) -> None:
    sections: list[str] = []
    length = len(preview.body) + len(RESOURCES_HEADING) + 2
    skipped: list[str] = []
    for rel in sorted(texts):
        path = PurePosixPath(rel)
        usable = path.parts[0] == "resources" and path.suffix.lower() in (".md", ".txt")
        if not usable:
            preview.excluded.append(Excluded(rel, "unused"))
            continue
        content = texts[rel].strip()
        section = f"### {rel}\n\n{content}"
        if length + len(section) + 2 > BODY_MAX:
            skipped.append(rel)
            preview.excluded.append(Excluded(rel, "too_long"))
            continue
        sections.append(section)
        length += len(section) + 2
        preview.kept.append(rel)
    if sections:
        preview.body = "\n\n".join([preview.body, RESOURCES_HEADING, *sections])
    if skipped:
        preview.warn(
            "resources_skipped",
            f"본문 한도({BODY_MAX:,}자)를 넘어 참고 자료 {len(skipped)}개는 넣지 못했어요.",
            preview.filename,
        )


# ── scan patterns ─────────────────────────────────────────────────────
#
# Each runs on one line of at most 4,000 characters; quantifiers are bounded.

_OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s{1,3}(?:all\s{1,3}|any\s{1,3})?(?:the\s{1,3})?"
    r"(?:previous|prior|above|earlier|preceding|system|original)\s{1,3}"
    r"(?:instructions?|rules?|prompts?|directions?|guidelines?)"
    r"|\byou\s{1,3}are\s{1,3}now\s{1,3}(?:in\s{1,3})?(?:developer|dan|jailbreak|god)\s{0,3}mode"
    r"|\b(?:reveal|print|show|leak)\s{1,3}(?:the\s{1,3}|your\s{1,3})?system\s{1,3}prompt"
    r"|\bdo\s{1,3}not\s{1,3}(?:tell|inform|let)\s{1,3}the\s{1,3}user"
    r"|(?:이전|앞의|위의|기존|원래|모든|시스템)\s{0,2}(?:의\s{0,2})?(?:지시|지침|규칙|명령|프롬프트)"
    r"(?:사항)?(?:을|를|은|는)?\s{0,2}(?:모두\s{0,2}|전부\s{0,2})?(?:무시|잊어|따르지)"
    r"|시스템\s{0,2}프롬프트(?:을|를)?\s{0,2}(?:공개|출력|보여|알려)"
    r"|사용자(?:에게|한테)\s{0,2}(?:알리지|말하지|숨기)",
    re.IGNORECASE,
)

_URL_OR_MAIL = re.compile(
    r"https?://[^\s<>\"')]{1,300}|\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,5}\b"
)
_SEND_WORDS = (
    "send",
    "post ",
    "upload",
    "forward",
    "email ",
    "e-mail",
    "mail to",
    "transmit",
    "exfiltrat",
    "webhook",
    "curl ",
    "wget ",
    "fetch(",
    "보내",
    "전송",
    "업로드",
    "올려",
    "제출",
    "포워딩",
)
_DATA_WORDS = (
    "data",
    "content",
    "conversation",
    "message",
    "file",
    "document",
    "history",
    "secret",
    "key",
    "token",
    "password",
    "credential",
    "user",
    "자료",
    "내용",
    "대화",
    "메시지",
    "파일",
    "문서",
    "기록",
    "비밀",
    "키",
    "토큰",
    "비밀번호",
    "개인정보",
    "사용자",
)


def _exfil_line(lines: list[str]) -> int | None:
    for number, line in enumerate(lines, 1):
        lower = line[:4_000].lower()
        if not any(w in lower for w in _SEND_WORDS):
            continue
        if not any(w in lower for w in _DATA_WORDS):
            continue
        if _URL_OR_MAIL.search(lower):
            return number
    return None


#: Bidi embeddings/overrides/isolates, marks, zero-width characters and a stray BOM.
_HIDDEN = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061c"
    "\u200b\u200c\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad"
)


def _hidden_line(lines: list[str]) -> int | None:
    for number, line in enumerate(lines, 1):
        # A zero-width joiner alone is how emoji sequences are written; it is not counted.
        if any(
            ch in _HIDDEN or (unicodedata.category(ch) == "Cf" and ch != "\u200d") for ch in line
        ):
            return number
    return None


_BASE64 = re.compile(
    r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{160,4000}={0,2}|data:[\w.+/-]{1,80};base64,"
)

_SECRET = re.compile(
    r"\bsk-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_-]{20,200}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,100}"
    r"|\bgithub_pat_[A-Za-z0-9_]{22,200}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,200}"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|\bglpat-[A-Za-z0-9_-]{20}"
    r"|-----BEGIN\s(?:RSA\s|EC\s|OPENSSH\s|DSA\s|PGP\s)?PRIVATE\sKEY-----"
    r"|\beyJ[A-Za-z0-9_-]{10,2000}\.eyJ[A-Za-z0-9_-]{10,2000}\.[A-Za-z0-9_-]{10,2000}"
    r"|(?:api[_-]?key|secret|access[_-]?token|auth[_-]?token|password|passwd|비밀번호)"
    r"[\"']?\s{0,3}[:=]\s{0,3}[\"']?[A-Za-z0-9_\-/+.]{16,200}",
    re.IGNORECASE,
)

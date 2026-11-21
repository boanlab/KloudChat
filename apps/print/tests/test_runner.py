"""The printer's subprocess runner and health signal, with stand-in programs for soffice.

Standard library only (plus the app's own dependencies). CI: `images` job, print.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers signal 0, but it is not running
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
    except OSError:
        return False


def _gone_soon(pid: int) -> bool:
    for _ in range(50):
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


class _Fake(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.bin = Path(self.dir.name) / "bin"
        self.bin.mkdir()
        patch = mock.patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        patch.start()
        self.addCleanup(patch.stop)

    def program(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


class TheRunnerKillsAndReapsEveryProcessItStarts(_Fake):
    def test_a_timeout_kills_the_grandchild_and_answers_504(self) -> None:
        pidfile = Path(self.dir.name) / "grandchild"
        # like oosplash starting soffice.bin: a child that outlives a plain kill of its parent
        self.program("slow", f"sleep 60 &\necho $! > {pidfile}\nwait\n")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main._run("slow", timeout=1))
        self.assertEqual(caught.exception.status_code, 504)
        self.assertTrue(_gone_soon(int(pidfile.read_text())))

    def test_a_finished_child_leaves_no_stray_descendant(self) -> None:
        pidfile = Path(self.dir.name) / "orphan"
        self.program("leaver", f"sleep 60 >/dev/null 2>&1 &\necho $! > {pidfile}\nexit 0\n")
        asyncio.run(main._run("leaver", timeout=5))
        self.assertTrue(_gone_soon(int(pidfile.read_text())))

    def test_a_failing_child_is_a_422_with_its_error_tail(self) -> None:
        self.program("broken", "echo nope >&2\nexit 3\n")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main._run("broken", timeout=5))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("nope", caught.exception.detail)

    def test_the_direct_child_is_reaped_after_a_timeout(self) -> None:
        pidfile = Path(self.dir.name) / "child"
        self.program("slow", f"echo $$ > {pidfile}\nexec sleep 60\n")
        with self.assertRaises(HTTPException):
            asyncio.run(main._run("slow", timeout=1))
        # reaped: no /proc entry at all, not even a defunct one
        self.assertFalse(Path(f"/proc/{int(pidfile.read_text())}").exists())


class AConversionThatWritesNothingIsRetriedThenAnsweredAsUnavailable(_Fake):
    def test_exit_zero_without_output_is_retried_once_with_a_fresh_profile_then_503(self) -> None:
        work = Path(self.dir.name) / "work"
        work.mkdir()
        log = Path(self.dir.name) / "calls"
        self.program("soffice", f'echo "$1" >> {log}\nexit 0\n')
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main._convert(work, "odp", work / "a.pptx", work / "a.odp"))
        self.assertEqual(caught.exception.status_code, 503)
        profiles = log.read_text().split()
        self.assertEqual(len(profiles), 2)
        self.assertNotEqual(profiles[0], profiles[1])

    def test_the_retry_succeeds_when_the_second_run_writes_the_file(self) -> None:
        work = Path(self.dir.name) / "work"
        work.mkdir()
        marker = Path(self.dir.name) / "first-done"
        self.program(
            "soffice",
            f"if [ -e {marker} ]; then touch {work}/a.odp; else touch {marker}; fi\nexit 0\n",
        )
        asyncio.run(main._convert(work, "odp", work / "a.pptx", work / "a.odp"))
        self.assertTrue((work / "a.odp").exists())


class TheHealthSignalCountsZombies(unittest.TestCase):
    def _proc(self, states: list[str]) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        for number, state in enumerate(states, start=1):
            entry = root / str(number)
            entry.mkdir()
            # a command name with spaces and a parenthesis must not fool the parser
            (entry / "stat").write_text(f"{number} (so ffice) ) {state} 1 1 1 0\n")
        (root / "self").mkdir()
        return root

    def test_only_processes_in_state_z_are_counted(self) -> None:
        self.assertEqual(main.zombie_count(self._proc(["S", "Z", "R", "Z", "Z"])), 3)

    def test_health_fails_with_503_above_the_threshold(self) -> None:
        with mock.patch.object(main, "zombie_count", return_value=main.MAX_ZOMBIES + 1):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.health())
        self.assertEqual(caught.exception.status_code, 503)

    def test_health_reports_the_count_while_below_the_threshold(self) -> None:
        browser = mock.Mock(is_connected=mock.Mock(return_value=True))
        with mock.patch.object(main, "zombie_count", return_value=2), \
                mock.patch.object(main.chromium, "page_context",
                                  mock.AsyncMock(return_value=browser)):
            self.assertEqual(asyncio.run(main.health()), {"ok": True, "zombies": 2})


if __name__ == "__main__":
    unittest.main()

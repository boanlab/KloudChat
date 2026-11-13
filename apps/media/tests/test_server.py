"""The media worker against its own ffmpeg, in its own image (CI: `images` job, media).

A 30-second clip is generated here — three scenes of different colours and shapes, a tone —
so no video is committed. Standard library only, like the server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


def _clip(path: str) -> None:
    """Three 10 s scenes (navy, a white box on green, maroon stripes) and a 440 Hz tone."""
    scenes = [
        "color=c=navy:s=320x180:d=10",
        "color=c=darkgreen:s=320x180:d=10,drawbox=x=40:y=40:w=120:h=80:color=white:t=fill",
        "color=c=maroon:s=320x180:d=10,drawgrid=w=40:h=40:t=6:c=white",
    ]
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for scene in scenes:
        args += ["-f", "lavfi", "-i", scene]
    args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=30",
             "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
             "-map", "[v]", "-map", "3:a", "-r", "10", "-c:v", "libvpx-vp9", "-b:v", "50k",
             "-c:a", "libopus", "-shortest", path]
    subprocess.run(args, check=True)


class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.root, "me"))
        _clip(os.path.join(cls.root, "me", "clip.webm"))
        server.ROOT = os.path.realpath(cls.root)
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()

    def post(self, path: str, body: dict) -> tuple[int, bytes]:
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_probe_reads_length_and_streams(self):
        status, body = self.post("/probe", {"key": "me/clip.webm"})
        info = json.loads(body)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(info["duration"], 30, delta=1)
        self.assertTrue(info["hasVideo"] and info["hasAudio"])
        self.assertEqual((info["width"], info["height"]), (320, 180))

    def test_scenes_find_each_change_once(self):
        status, body = self.post("/scenes", {"key": "me/clip.webm", "max_gap": 60})
        times = [f["t"] for f in json.loads(body)["frames"]]
        self.assertEqual(status, 200)
        self.assertEqual(len(times), 3, times)
        self.assertEqual(times[0], 0.0)
        self.assertAlmostEqual(times[1], 10, delta=1.5)
        self.assertAlmostEqual(times[2], 20, delta=1.5)

    def test_audio_is_a_16k_mono_flac_piece(self):
        status, body = self.post("/audio", {"key": "me/clip.webm", "start": 5, "duration": 10})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"fLaC"))
        path = os.path.join(self.root, "me", "piece.flac")
        with open(path, "wb") as handle:
            handle.write(body)
        found = json.loads(server.run(["ffprobe", "-v", "error", "-print_format", "json",
                                       "-show_streams", "-show_format", path]))
        stream = found["streams"][0]
        self.assertEqual((stream["sample_rate"], stream["channels"]), ("16000", 1))
        # the header states the length (Whisper refuses a FLAC that does not)
        self.assertAlmostEqual(float(found["format"]["duration"]), 10, delta=0.5)

    def test_a_frame_is_a_jpeg_no_wider_than_asked(self):
        status, body = self.post("/frame", {"key": "me/clip.webm", "t": 12, "width": 160})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\xff\xd8"))

    def test_keys_outside_the_root_and_unreadable_files_are_refused(self):
        self.assertEqual(self.post("/probe", {"key": "../etc/passwd"})[0], 400)
        self.assertEqual(self.post("/probe", {"key": "me/none.mp4"})[0], 404)
        with open(os.path.join(self.root, "me", "junk.mp4"), "wb") as handle:
            handle.write(b"not a video at all")
        self.assertEqual(self.post("/probe", {"key": "me/junk.mp4"})[0], 422)


if __name__ == "__main__":
    unittest.main()

import http.client
import json
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch

from server import DashboardHandler, DashboardServer, build_ffmpeg_command
import server
import loop_video


class StreamKeyApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = DashboardServer(("127.0.0.1", 0), DashboardHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        server.STREAM_KEY = None
        server.STREAM_FORMAT = "horizontal"

    def request(self, method, path, body=None, headers=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        request_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if headers:
            request_headers.update(headers)
        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(body)
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded_body, headers=request_headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def same_origin(self):
        return {"Origin": f"http://127.0.0.1:{self.port}"}

    def test_key_is_accepted_but_never_returned(self):
        status, body = self.request(
            "POST",
            "/api/stream-key",
            {"key": "private-test-key"},
            self.same_origin(),
        )
        self.assertEqual(status, 200)
        self.assertNotIn(b"private-test-key", body)

        status, body = self.request("GET", "/api/stream-key")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "configured": True,
            "managed_by_environment": False,
            "removable": True,
        })

    def test_remove_clears_the_key(self):
        server.STREAM_KEY = "private-test-key"
        status, body = self.request("DELETE", "/api/stream-key", headers=self.same_origin())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"configured": False})
        self.assertIsNone(server.STREAM_KEY)

    def test_rejects_cross_origin_and_invalid_keys(self):
        status, _ = self.request(
            "POST",
            "/api/stream-key",
            {"key": "private-test-key"},
            {"Origin": "https://example.com"},
        )
        self.assertEqual(status, 403)

        status, _ = self.request(
            "POST",
            "/api/stream-key",
            {"key": " "},
            self.same_origin(),
        )
        self.assertEqual(status, 400)
        self.assertIsNone(server.STREAM_KEY)

    def test_stream_key_status_never_reveals_the_key(self):
        server.STREAM_KEY = "private-test-key"
        status, body = self.request("GET", "/api/stream-key", host="dashboard.example")
        self.assertEqual(status, 200)
        self.assertNotIn(b"private-test-key", body)
        self.assertTrue(json.loads(body)["configured"])

    def test_stream_format_can_be_selected_only_while_broadcast_is_stopped(self):
        status, body = self.request("GET", "/api/stream-format")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"format": "horizontal", "width": 1280, "height": 720})

        status, body = self.request(
            "POST",
            "/api/stream-format",
            {"format": "vertical"},
            self.same_origin(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"format": "vertical", "width": 720, "height": 1280})
        self.assertEqual(server.STREAM_FORMAT, "vertical")

        status, _ = self.request(
            "POST",
            "/api/stream-format",
            {"format": "square"},
            self.same_origin(),
        )
        self.assertEqual(status, 400)
        self.assertEqual(server.STREAM_FORMAT, "vertical")

        class ActiveWorker:
            def is_requested(self):
                return True

        self.httpd.stream_worker = ActiveWorker()
        try:
            status, body = self.request(
                "POST",
                "/api/stream-format",
                {"format": "horizontal"},
                self.same_origin(),
            )
            self.assertEqual(status, 409)
            self.assertEqual(server.STREAM_FORMAT, "vertical")
        finally:
            del self.httpd.stream_worker

    def test_rejects_unlisted_files(self):
        status, _ = self.request("GET", "/server.py")
        self.assertEqual(status, 404)

    def test_dashboard_uses_a_live_local_clock_and_avoids_fake_analytics(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="currentTime"', page)
        self.assertIn(b'id="greeting"', page)
        self.assertIn(b'id="encoderStatus"', page)
        self.assertIn(b"Live analytics unavailable", page)
        self.assertNotIn(b"1,284", page)
        self.assertNotIn(b"1,562", page)
        self.assertNotIn(b"3,849", page)
        self.assertNotIn(b"2:34 PM", page)

        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"setInterval(updateLocalDateTime, 1000)", script)
        self.assertIn(b"localTimeFormatter.format(now)", script)

    def test_hosted_dashboard_can_control_stream_only_from_its_https_origin(self):
        class FakeWorker:
            def __init__(self):
                self.started = False
                self.stopped = False

            def request_start(self):
                self.started = True
                return True

            def request_stop(self):
                self.stopped = True

        worker = FakeWorker()
        self.httpd.stream_worker = worker
        server.STREAM_KEY = "private-test-key"
        try:
            status, body = self.request(
                "POST",
                "/api/stream/start",
                headers={"Origin": "https://dashboard.example"},
                host="dashboard.example",
            )
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(body), {"state": "starting"})
            self.assertTrue(worker.started)

            status, body = self.request(
                "POST",
                "/api/stream/stop",
                headers={"Origin": "https://dashboard.example"},
                host="dashboard.example",
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"state": "idle"})
            self.assertTrue(worker.stopped)
        finally:
            del self.httpd.stream_worker

    def test_saving_key_does_not_start_stream_until_start_is_clicked(self):
        worker = server.StreamWorker()
        self.httpd.stream_worker = worker
        server.STREAM_KEY = None
        try:
            with patch.dict(os.environ, {"YOUTUBE_STREAM_KEY": ""}):
                status, _ = self.request(
                    "POST",
                    "/api/stream-key",
                    {"key": "private-test-key"},
                    self.same_origin(),
                )
                self.assertEqual(status, 200)
                self.assertFalse(worker.is_requested())

                status, _ = self.request(
                    "POST",
                    "/api/stream/start",
                    headers=self.same_origin(),
                )
                self.assertEqual(status, 202)
                self.assertTrue(worker.is_requested())

                status, _ = self.request(
                    "POST",
                    "/api/stream/stop",
                    headers=self.same_origin(),
                )
                self.assertEqual(status, 200)
                self.assertFalse(worker.is_requested())
        finally:
            del self.httpd.stream_worker
            server.STREAM_KEY = None

    def test_start_rejects_missing_key_and_cross_origin_requests(self):
        class FakeWorker:
            def __init__(self):
                self.start_attempted = False

            def request_start(self):
                self.start_attempted = True
                return False

        worker = FakeWorker()
        self.httpd.stream_worker = worker
        try:
            with patch.dict(os.environ, {"YOUTUBE_STREAM_KEY": ""}):
                status, _ = self.request(
                    "POST",
                    "/api/stream/start",
                    headers={"Origin": "https://dashboard.example"},
                    host="dashboard.example",
                )
                self.assertEqual(status, 409)
                self.assertTrue(worker.start_attempted)

            server.STREAM_KEY = "private-test-key"
            status, _ = self.request(
                "POST",
                "/api/stream/start",
                headers={"Origin": "https://attacker.example"},
                host="dashboard.example",
            )
            self.assertEqual(status, 403)
            status, _ = self.request(
                "POST",
                "/api/stream/start",
                headers={"Origin": "http://dashboard.example"},
                host="dashboard.example",
            )
            self.assertEqual(status, 403)
        finally:
            del self.httpd.stream_worker

    def test_loop_video_route_supports_browser_byte_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "preview.mp4"
            video_path.write_bytes(b"0123456789")
            with patch.dict(os.environ, {"VIDEO_PATH": str(video_path)}):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                connection.request(
                    "GET",
                    "/api/loop-video",
                    headers={
                        "Host": f"127.0.0.1:{self.port}",
                        "Range": "bytes=3-6",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.getheader("Content-Range"), "bytes 3-6/10")
                self.assertEqual(response.read(), b"3456")
                connection.close()

    def test_preview_api_generates_and_serves_a_fresh_unique_design_each_request(self):
        created = []

        def fake_generate(path, ffmpeg, seed, duration_seconds, fps, width, height):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(f"design-{seed}".encode("ascii"))
            created.append((seed, duration_seconds, fps, width, height, Path(path)))

        with patch.object(
            server,
            "next_design_seed",
            side_effect=[0x202611110001, 0x202611110002],
        ), patch.object(
            server,
            "find_ffmpeg",
            return_value="ffmpeg-test",
        ), patch.object(
            server,
            "generate_loop_video",
            side_effect=fake_generate,
        ):
            try:
                status_one, body_one = self.request(
                    "POST",
                    "/api/preview",
                    headers=self.same_origin(),
                )
                status_two, body_two = self.request(
                    "POST",
                    "/api/preview",
                    headers=self.same_origin(),
                )
                first = json.loads(body_one)
                second = json.loads(body_two)
                self.assertEqual(status_one, 200)
                self.assertEqual(status_two, 200)
                self.assertNotEqual(first["design_id"], second["design_id"])
                self.assertTrue(first["video_url"].startswith("/api/loop-video?design="))
                self.assertTrue(second["video_url"].startswith("/api/loop-video?design="))
                self.assertEqual(created[0][1:5], (12, 8, 1280, 720))
                self.assertEqual(created[1][1:5], (12, 8, 1280, 720))

                status, video_bytes = self.request(
                    "GET",
                    first["video_url"],
                    headers={"Range": "bytes=0-31"},
                )
                self.assertEqual(status, 206)
                self.assertEqual(video_bytes, f"design-{created[0][0]}".encode("ascii"))
            finally:
                with server._PREVIEW_LOCK:
                    for seed, _, _, _, _, path in created:
                        server._PREVIEW_VIDEOS.pop(f"{seed:016X}", None)
                        path.unlink(missing_ok=True)

    def test_stream_status_is_public_safe_and_does_not_disclose_key(self):
        server.STREAM_KEY = "private-test-key"
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "generated.mp4"
            with patch.dict(os.environ, {"GENERATED_VIDEO_PATH": str(video_path), "VIDEO_PATH": ""}):
                status, body = self.request(
                    "GET",
                    "/api/stream-status",
                    host="dashboard.example",
                )
                self.assertEqual(status, 200)
                self.assertNotIn(b"private-test-key", body)
                result = json.loads(body)
                self.assertTrue(result["configured"])
                self.assertFalse(result["video_configured"])
                self.assertEqual(result["video_mode"], "generated")
                self.assertEqual(result["format"], "horizontal")
                self.assertIsNone(result["design_id"])
                self.assertEqual(result["design_number"], 0)
                Path(directory, "generated-horizontal.mp4").write_bytes(b"generated")

                status, body = self.request(
                    "GET",
                    "/api/stream-status",
                    host="dashboard.example",
                )
                self.assertTrue(json.loads(body)["video_configured"])

    def test_ffmpeg_command_repeats_video_and_targets_rtmp(self):
        command = build_ffmpeg_command(
            "/media/stream.mp4",
            "rtmp://a.rtmp.youtube.com/live2",
            "private-test-key",
            ffmpeg="/opt/ffmpeg/bin/ffmpeg",
        )
        self.assertEqual(command[0], "/opt/ffmpeg/bin/ffmpeg")
        self.assertIn("-stream_loop", command)
        self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
        self.assertIn("-re", command)
        self.assertEqual(command[-1], "rtmp://a.rtmp.youtube.com/live2/private-test-key")
        self.assertIn("0:a:0?", command)
        video_filter = command[command.index("-vf") + 1]
        self.assertIn("scale=1280:720:force_original_aspect_ratio=decrease", video_filter)

    def test_ffmpeg_command_scales_vertical_output_and_pads_to_portrait(self):
        command = build_ffmpeg_command(
            "/media/stream.mp4",
            "rtmp://a.rtmp.youtube.com/live2",
            "private-test-key",
            video_format="vertical",
        )
        self.assertIn(
            "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
            command,
        )

    def test_worker_starts_loop_automatically_from_server_environment(self):
        class FakeProcess:
            def __init__(self):
                self.terminated = threading.Event()

            def poll(self):
                return 0 if self.terminated.is_set() else None

            def terminate(self):
                self.terminated.set()

            def wait(self, timeout=None):
                self.terminated.wait(timeout)
                return 0

        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "stream.mp4")
            with open(video, "wb") as stream:
                stream.write(b"test")

            fake_process = FakeProcess()
            with patch.dict(
                os.environ,
                {
                    "YOUTUBE_STREAM_KEY": "private-test-key",
                    "VIDEO_PATH": video,
                    "YOUTUBE_RTMP_URL": "rtmp://example.test/live",
                },
            ), patch.object(server, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), patch.object(
                server.subprocess, "Popen", return_value=fake_process
            ) as popen, patch.object(server, "STREAM_STARTUP_GRACE_SECONDS", 0):
                worker = server.StreamWorker()
                self.assertTrue(worker.request_start())
                worker.start()
                deadline = time.monotonic() + 2
                while worker.status() != "streaming" and time.monotonic() < deadline:
                    time.sleep(0.01)

                self.assertEqual(worker.status(), "streaming")
                command = popen.call_args.args[0]
                self.assertEqual(command[0], "/usr/bin/ffmpeg")
                self.assertIn("-1", command)
                self.assertEqual(command[-1], "rtmp://example.test/live/private-test-key")
                self.assertEqual(command[command.index("-vf") + 1].split(",", 1)[0], "scale=1280:720:force_original_aspect_ratio=decrease")

                worker.stop()
                self.assertEqual(worker.status(), "stopped")
                self.assertTrue(fake_process.terminated.is_set())

    def test_worker_streams_generated_frames_and_starts_new_designs(self):
        class FakeProcess:
            def __init__(self):
                self.terminated = threading.Event()
                self.stdin = self
                self.closed = False
                self.command = None

            def poll(self):
                return 0 if self.terminated.is_set() else None

            def write(self, frame):
                return len(frame)

            def close(self):
                self.closed = True

            def terminate(self):
                self.terminated.set()

            def wait(self, timeout=None):
                self.terminated.wait(timeout)
                return 0

        with tempfile.TemporaryDirectory() as directory:
            server.STREAM_FORMAT = "vertical"
            fake_process = FakeProcess()
            started = threading.Event()

            def stream_frames(process, width, height, should_continue, fps, on_frame, on_design):
                self.assertIs(process, fake_process)
                self.assertEqual((width, height), (720, 1280))
                self.assertEqual(fps, loop_video.LIVE_FPS)
                on_design(0x202611110001)
                on_frame()
                started.set()
                while should_continue():
                    time.sleep(0.005)

            with patch.dict(
                os.environ,
                {
                    "YOUTUBE_STREAM_KEY": "private-test-key",
                    "VIDEO_PATH": "",
                    "YOUTUBE_RTMP_URL": "rtmp://example.test/live",
                },
            ), patch.object(server, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), patch.object(
                server, "stream_generated_designs", side_effect=stream_frames
            ) as generate, patch.object(
                server.subprocess, "Popen", return_value=fake_process
            ) as popen, patch.object(server, "STREAM_STARTUP_GRACE_SECONDS", 0):
                worker = server.StreamWorker()
                self.assertTrue(worker.request_start())
                worker.start()
                self.assertTrue(started.wait(2))
                self.assertEqual(worker.status(), "streaming")
                generate.assert_called_once()
                command = popen.call_args.args[0]
                self.assertIn("rawvideo", command)
                self.assertIn("720x1280", command)
                self.assertEqual(command[-1], "rtmp://example.test/live/private-test-key")
                self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)
                self.assertEqual(
                    worker.current_design(),
                    {"design_id": f"{0x202611110001:016X}", "design_number": 1},
                )

                worker.stop()
                self.assertTrue(fake_process.terminated.is_set())


class GeneratedLoopVideoTests(unittest.TestCase):
    def test_design_seeds_are_unique_across_calls_and_concurrent_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            sequence_path = os.path.join(directory, "design-sequence.txt")
            with patch.dict(os.environ, {"ATG_DESIGN_SEQUENCE_PATH": sequence_path}):
                first = loop_video.next_design_seed()
                with ThreadPoolExecutor(max_workers=8) as executor:
                    concurrent = list(executor.map(lambda _index: loop_video.next_design_seed(), range(16)))
                later = loop_video.next_design_seed()

            all_designs = [first, *concurrent, later]
            self.assertEqual(len(all_designs), len(set(all_designs)))
            self.assertGreater(later, max([first, *concurrent]))
            self.assertEqual(int(Path(sequence_path).read_text(encoding="ascii").strip()), later)

    def test_animation_frames_change_and_have_a_smooth_loop_seam(self):
        frame_count = 64
        frames = [
            loop_video.render_animation_frame(
                seed=10,
                frame_index=index,
                total_frames=frame_count,
                width=320,
                height=180,
            )
            for index in (0, 1, frame_count - 1)
        ]
        self.assertEqual(len(frames[0]), 320 * 180 * 3)
        self.assertNotEqual(frames[0], frames[1])
        first_pixels = tuple(frames[0])
        last_pixels = tuple(frames[2])
        average_seam_difference = sum(abs(a - b) for a, b in zip(first_pixels, last_pixels)) / len(first_pixels)
        self.assertLess(average_seam_difference, 24)

    def test_animation_has_four_distinct_original_scene_compositions(self):
        frame_count = 64
        scene_frames = [
            loop_video.render_animation_frame(
                seed=27,
                frame_index=index,
                total_frames=frame_count,
                width=320,
                height=180,
            )
            for index in (0, 16, 32, 48)
        ]
        self.assertEqual(len(set(scene_frames)), 4)
        self.assertEqual(loop_video.DURATION_SECONDS, 60)
        self.assertEqual(len(loop_video.SCENE_TITLES), 4)

    def test_randomized_seed_creates_more_than_the_old_fixed_color_choices(self):
        palettes = {
            loop_video._animation_assets(seed, 320, 180)[0]
            for seed in range(1000, 1024)
        }
        self.assertGreater(len(palettes), len(loop_video.PALETTES))

    def test_video_formats_produce_horizontal_and_vertical_frame_dimensions(self):
        self.assertEqual(loop_video.video_dimensions("horizontal"), (1280, 720))
        self.assertEqual(loop_video.video_dimensions("vertical"), (720, 1280))
        portrait_frame = loop_video.render_animation_frame(
            seed=14,
            frame_index=0,
            total_frames=24,
            width=720,
            height=1280,
        )
        self.assertEqual(len(portrait_frame), 720 * 1280 * 3)

    def test_video_command_reads_live_animation_frames_and_includes_audio(self):
        command = loop_video.build_video_command(
            "/usr/bin/ffmpeg",
            "generated.mp4",
            width=320,
            height=180,
            fps=8,
            duration_seconds=4,
        )
        self.assertIn("rawvideo", command)
        self.assertIn("pipe:0", command)
        self.assertIn("320x180", command)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=44100", command)
        self.assertEqual(command[-1], "generated.mp4")

    def test_generated_stream_command_is_an_open_live_raw_video_input(self):
        command = loop_video.build_generated_stream_command(
            "/usr/bin/ffmpeg",
            720,
            1280,
            "rtmp://example.test/live",
            "private-test-key",
            fps=8,
        )
        self.assertIn("rawvideo", command)
        self.assertIn("pipe:0", command)
        self.assertIn("720x1280", command)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=44100", command)
        self.assertEqual(command[command.index("-r") + 1], "30")
        self.assertNotIn("-stream_loop", command)
        self.assertNotIn("-t", command)
        self.assertEqual(command[-1], "rtmp://example.test/live/private-test-key")

    def test_live_renderer_randomizes_each_sixty_second_design_segment(self):
        class FakeInput:
            def __init__(self):
                self.frames = []

            def write(self, frame):
                self.frames.append(frame)
                return len(frame)

        class FakeProcess:
            def __init__(self):
                self.stdin = FakeInput()

            def poll(self):
                return None

        process = FakeProcess()
        render_calls = []
        with patch.object(
            loop_video,
            "render_animation_frame",
            side_effect=lambda seed, frame, total, **_kwargs: (
                render_calls.append((seed, frame, total)) or b"frame"
            ),
        ), patch.object(loop_video, "next_design_seed", side_effect=[20260001, 20260002]), patch.object(
            loop_video.time, "sleep"
        ):
            loop_video.stream_generated_designs(
                process,
                width=160,
                height=90,
                should_continue=lambda: len(process.stdin.frames) < 120,
                fps=1,
            )

        seeds = [render_calls[index][0] for index in (0, 60)]
        self.assertNotEqual(seeds[0], seeds[1])
        self.assertEqual([call[1] for call in render_calls], list(range(60)) * 2)
        self.assertTrue(all(call[2] == 60 for call in render_calls))
        self.assertEqual(loop_video.DURATION_SECONDS, 60)

    def test_generate_loop_video_encodes_generated_raw_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "loop.mp4")

            class FakeProcess:
                def __init__(self, command, **_kwargs):
                    self.command = command
                    self.stdin = self
                    self.input_size = 0
                    self.closed = False
                    self.returncode = None

                def write(self, frame):
                    self.input_size += len(frame)
                    return len(frame)

                def close(self):
                    self.closed = True

                def wait(self, timeout=None):
                    Path(self.command[-1]).write_bytes(b"fake animated mp4")
                    self.returncode = 0
                    return self.returncode

            processes = []

            def fake_popen(command, **kwargs):
                process = FakeProcess(command, **kwargs)
                processes.append(process)
                return process

            with patch.object(loop_video, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), patch.object(
                loop_video.subprocess, "Popen", side_effect=fake_popen
            ):
                result = loop_video.generate_loop_video(
                    output,
                    seed=123,
                    duration_seconds=2,
                    fps=3,
                    width=160,
                    height=90,
                )

            self.assertEqual(result, Path(output))
            self.assertGreater(Path(output).stat().st_size, 0)
            self.assertEqual(len(processes), 1)
            self.assertEqual(processes[0].input_size, 6 * 160 * 90 * 3)
            self.assertIn("rawvideo", processes[0].command)

    def test_worker_waits_for_explicit_start_before_generating_or_streaming(self):
        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "generated.mp4")

            def create_fake_video(path, ffmpeg):
                self.assertEqual(ffmpeg, "/usr/bin/ffmpeg")
                Path(path).write_bytes(b"generated video")

            with patch.dict(
                os.environ,
                {
                    "YOUTUBE_STREAM_KEY": "",
                    "VIDEO_PATH": "",
                    "GENERATED_VIDEO_PATH": video,
                },
            ), patch.object(server, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), patch.object(
                server, "generate_loop_video", side_effect=create_fake_video
            ) as generate, patch.object(server.subprocess, "Popen") as popen:
                worker = server.StreamWorker()
                worker.start()
                deadline = time.monotonic() + 2
                while worker.status() != "idle" and time.monotonic() < deadline:
                    time.sleep(0.01)

                self.assertEqual(worker.status(), "idle")
                self.assertFalse(worker.request_start())
                generate.assert_not_called()
                popen.assert_not_called()
                worker.stop()


if __name__ == "__main__":
    unittest.main()

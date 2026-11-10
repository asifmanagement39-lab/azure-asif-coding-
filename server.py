import json
import os
import signal
import subprocess
import threading
import tempfile
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from loop_video import (
    LIVE_FPS,
    build_generated_stream_command,
    find_ffmpeg,
    generate_loop_video,
    next_design_seed,
    stream_generated_designs,
    video_dimensions,
)


ROOT = Path(__file__).resolve().parent
MAX_KEY_LENGTH = 512
MAX_BODY_LENGTH = 2048
STREAM_STARTUP_GRACE_SECONDS = 10
STREAM_KEY = None
STREAM_FORMAT = "horizontal"
_STREAM_CONFIG_LOCK = threading.RLock()
_PREVIEW_LOCK = threading.Lock()
_PREVIEW_VIDEOS = OrderedDict()
PREVIEW_CACHE_SIZE = 8


def build_ffmpeg_command(video_path, rtmp_url, stream_key, ffmpeg="ffmpeg", video_format="horizontal"):
    destination = f"{rtmp_url.rstrip('/')}/{stream_key}"
    width, height = video_dimensions(video_format)
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-re",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-g",
        "60",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "44100",
        "-f",
        "flv",
        destination,
    ]


def build_generated_ffmpeg_command(rtmp_url, stream_key, ffmpeg="ffmpeg", video_format="horizontal"):
    width, height = video_dimensions(video_format)
    return build_generated_stream_command(
        ffmpeg,
        width,
        height,
        rtmp_url,
        stream_key,
        fps=LIVE_FPS,
    )


def get_stream_format():
    with _STREAM_CONFIG_LOCK:
        return STREAM_FORMAT


def generated_video_path(video_format):
    base_path = Path(
        os.environ.get(
            "GENERATED_VIDEO_PATH",
            str(Path(tempfile.gettempdir()) / "asif-tech-global-loop.mp4"),
        )
    )
    suffix = base_path.suffix or ".mp4"
    if base_path.stem.endswith(f"-{video_format}"):
        return base_path
    return base_path.with_name(f"{base_path.stem}-{video_format}{suffix}")


class StreamWorker:
    def __init__(self):
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._requested = threading.Event()
        self._state = "idle"
        self._last_error = None
        self._design_seed = None
        self._design_count = 0
        self._process = None
        self._thread = threading.Thread(target=self._run, name="youtube-loop-stream", daemon=True)

    def start(self):
        self._thread.start()

    def status(self):
        with self._lock:
            return self._state

    def last_error(self):
        with self._lock:
            return self._last_error

    def current_design(self):
        with self._lock:
            return {
                "design_id": f"{self._design_seed:016X}" if self._design_seed is not None else None,
                "design_number": self._design_count,
            }

    def is_requested(self):
        return self._requested.is_set()

    def notify_config_changed(self):
        self._wake.set()

    def request_start(self):
        with _STREAM_CONFIG_LOCK:
            if self._stopping.is_set():
                return False
            if not (STREAM_KEY or os.environ.get("YOUTUBE_STREAM_KEY", "").strip()):
                return False
            self._requested.set()
            self._wake.set()
            return True

    def request_stop(self):
        self._requested.clear()
        self._wake.set()
        with self._lock:
            process = self._process
            self._state = "stopping" if process is not None else "idle"
        if process is not None:
            self._stop_process(process)

    def _set_state(self, state):
        with self._lock:
            self._state = state

    def _run(self):
        backoff = 2
        while not self._stopping.is_set():
            if not self._requested.is_set():
                self._set_state("idle")
                self._wake.wait()
                self._wake.clear()
                continue

            stream_key = STREAM_KEY or os.environ.get("YOUTUBE_STREAM_KEY", "").strip()
            custom_video = os.environ.get("VIDEO_PATH", "").strip()
            stream_format = get_stream_format()
            rtmp_url = os.environ.get(
                "YOUTUBE_RTMP_URL", "rtmp://a.rtmp.youtube.com/live2"
            ).strip()

            if custom_video and not Path(custom_video).is_file():
                self._set_state("needs_video")
                self._wake.wait(1)
                self._wake.clear()
                continue
            ffmpeg = find_ffmpeg()
            if ffmpeg is None:
                self._set_state("ffmpeg_unavailable")
                with self._lock:
                    self._last_error = "No FFmpeg executable was found on the server."
                self._wake.wait(5)
                self._wake.clear()
                continue

            if not stream_key:
                self._set_state("needs_stream_key")
                self._wake.wait(1)
                self._wake.clear()
                continue

            if not rtmp_url.startswith(("rtmp://", "rtmps://")) or any(
                char.isspace() for char in rtmp_url
            ):
                self._set_state("invalid_rtmp_url")
                self._wake.wait(5)
                self._wake.clear()
                continue

            self._set_state("starting")
            with self._lock:
                self._last_error = None
            generated_video = not custom_video
            command = (
                build_generated_ffmpeg_command(
                    rtmp_url,
                    stream_key,
                    ffmpeg=ffmpeg,
                    video_format=stream_format,
                )
                if generated_video
                else build_ffmpeg_command(
                    custom_video,
                    rtmp_url,
                    stream_key,
                    ffmpeg=ffmpeg,
                    video_format=stream_format,
                )
            )
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if generated_video else subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    close_fds=True,
                )
            except OSError:
                self._set_state("ffmpeg_unavailable")
                with self._lock:
                    self._last_error = "FFmpeg could not be launched; verify the encoder path and permissions."
                self._wake.wait(5)
                self._wake.clear()
                continue

            with self._lock:
                self._process = process
                self._state = "starting"

            started_at = time.monotonic()
            if generated_video:
                design_error = False

                def should_continue():
                    current_key = STREAM_KEY or os.environ.get("YOUTUBE_STREAM_KEY", "").strip()
                    return (
                        not self._stopping.is_set()
                        and self._requested.is_set()
                        and current_key == stream_key
                        and process.poll() is None
                    )

                def update_stream_state():
                    if time.monotonic() - started_at >= STREAM_STARTUP_GRACE_SECONDS:
                        self._set_state("streaming")

                def update_current_design(seed):
                    with self._lock:
                        self._design_seed = seed
                        self._design_count += 1

                width, height = video_dimensions(stream_format)
                try:
                    stream_generated_designs(
                        process,
                        width,
                        height,
                        should_continue,
                        fps=LIVE_FPS,
                        on_frame=update_stream_state,
                        on_design=update_current_design,
                    )
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    print(f"Live design generation failed: {error}", flush=True)
                    self._set_state("design_generation_failed")
                    with self._lock:
                        self._last_error = (
                            "A unique ATG design could not be created; check server storage and logs."
                        )
                    design_error = True
                    self._stop_process(process)
                finally:
                    if process.stdin and not process.stdin.closed:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass
                if process.poll() is None:
                    self._stop_process(process)
            else:
                while process.poll() is None:
                    if self._stopping.is_set() or not self._requested.is_set():
                        self._stop_process(process)
                        break
                    current_key = STREAM_KEY or os.environ.get("YOUTUBE_STREAM_KEY", "").strip()
                    if current_key != stream_key:
                        self._stop_process(process)
                        break
                    if time.monotonic() - started_at >= STREAM_STARTUP_GRACE_SECONDS:
                        self._set_state("streaming")
                    self._wake.wait(1)
                    self._wake.clear()

            with self._lock:
                self._process = None
                return_code = process.poll()

            if generated_video and design_error:
                if self._stopping.is_set() or not self._requested.is_set():
                    continue
                self._set_state("design_generation_failed")
                self._wake.wait(5)
                self._wake.clear()
                continue

            if self._stopping.is_set():
                break

            if not self._requested.is_set():
                self._set_state("idle")
                continue

            if self._wake.is_set():
                self._wake.clear()
                continue

            if not (STREAM_KEY or os.environ.get("YOUTUBE_STREAM_KEY", "").strip()):
                self._set_state("needs_stream_key")
                self._requested.clear()
                continue

            if return_code is not None:
                with self._lock:
                    self._last_error = (
                        f"FFmpeg exited with code {return_code}. Check the stream key, "
                        "server network, and YouTube Live event settings."
                    )

            self._set_state("reconnecting")
            self._wake.wait(backoff)
            self._wake.clear()
            backoff = min(backoff * 2, 60)
        self._set_state("stopped")

    @staticmethod
    def _stop_process(process):
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=10)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait()
            except ProcessLookupError:
                return

    def stop(self):
        self._stopping.set()
        self._wake.set()
        with self._lock:
            process = self._process
        if process is not None:
            self._stop_process(process)
        self._thread.join(timeout=12)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AsifTechGlobal/1.0"
    sys_version = ""

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _is_local_request(self, require_origin=False):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        local_hosts = {
            f"127.0.0.1:{port}".casefold(),
            f"localhost:{port}".casefold(),
        }
        if origin is None:
            return not require_origin and host.casefold() in local_hosts

        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme not in ("http", "https")
            or not parsed_origin.netloc
            or parsed_origin.netloc.casefold() != host.casefold()
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path not in ("", "/")
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            return False
        return parsed_origin.scheme == "https" or host.casefold() in local_hosts

    def _read_json(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json."})
            return None

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"error": "A valid Content-Length is required."})
            return None

        if length < 1 or length > MAX_BODY_LENGTH:
            self._send_json(413, {"error": "Request body is empty or too large."})
            return None

        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Request body must be valid JSON."})
            return None

        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Request body must be a JSON object."})
            return None
        return payload

    def do_GET(self):
        parsed_path = urlsplit(self.path)
        path = parsed_path.path
        if path == "/api/loop-video":
            custom_video = os.environ.get("VIDEO_PATH", "").strip()
            preview_id = parse_qs(parsed_path.query).get("design", [None])[0]
            if preview_id is not None:
                with _PREVIEW_LOCK:
                    video_path = _PREVIEW_VIDEOS.get(preview_id)
                if video_path is None:
                    self._send_json(404, {"error": "This preview design has expired. Load a fresh preview."})
                    return
            else:
                stream_format = get_stream_format()
                video_path = Path(custom_video) if custom_video else generated_video_path(stream_format)
            try:
                file_size = video_path.stat().st_size
            except FileNotFoundError:
                self._send_json(404, {"error": "The loop video is not ready yet."})
                return
            if not video_path.is_file() or file_size == 0:
                self._send_json(404, {"error": "The loop video is not ready yet."})
                return

            start = 0
            end = file_size - 1
            status = 200
            range_header = self.headers.get("Range")
            if range_header:
                if not range_header.startswith("bytes=") or "," in range_header:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                range_start, separator, range_end = range_header[6:].partition("-")
                if not separator:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                try:
                    if range_start:
                        start = int(range_start)
                        end = min(int(range_end), end) if range_end else end
                    elif range_end:
                        suffix_length = int(range_end)
                        start = max(0, file_size - suffix_length)
                    else:
                        raise ValueError("Empty byte range.")
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if start > end or start >= file_size or start < 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206

            content_length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if preview_id is not None:
                self.send_header("X-ATG-Design-ID", preview_id)
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            try:
                with video_path.open("rb") as video:
                    video.seek(start)
                    remaining = content_length
                    while remaining:
                        chunk = video.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/stream-format":
            stream_format = get_stream_format()
            width, height = video_dimensions(stream_format)
            self._send_json(
                200,
                {"format": stream_format, "width": width, "height": height},
            )
            return
        if path == "/api/stream-key":
            environment_key = bool(os.environ.get("YOUTUBE_STREAM_KEY", "").strip())
            self._send_json(
                200,
                {
                    "configured": bool(STREAM_KEY or environment_key),
                    "managed_by_environment": environment_key and STREAM_KEY is None,
                    "removable": STREAM_KEY is not None,
                },
            )
            return
        if path == "/api/stream-status":
            stream_key = STREAM_KEY or os.environ.get("YOUTUBE_STREAM_KEY", "").strip()
            custom_video = os.environ.get("VIDEO_PATH", "").strip()
            generated_path = generated_video_path(get_stream_format())
            worker = getattr(self.server, "stream_worker", None)
            state = worker.status() if worker is not None else "stopped"
            start_requested = worker.is_requested() if worker is not None else False
            last_error = worker.last_error() if worker is not None else None
            current_design = (
                worker.current_design()
                if worker is not None and hasattr(worker, "current_design")
                else {"design_id": None, "design_number": 0}
            )
            self._send_json(
                200,
                {
                    "configured": bool(stream_key),
                    "state": state,
                    "start_requested": start_requested,
                    "last_error": last_error,
                    "video_configured": (
                        Path(custom_video).is_file() if custom_video else generated_path.is_file()
                    ),
                    "video_mode": "custom" if custom_video else "generated",
                    "format": get_stream_format(),
                    "design_id": current_design["design_id"],
                    "design_number": current_design["design_number"],
                },
            )
            return
        if path == "/":
            path = "/index.html"

        assets = {
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        asset = assets.get(path)
        if asset is None:
            self._send_json(404, {"error": "Not found."})
            return

        filename, content_type = asset
        try:
            body = (ROOT / filename).read_bytes()
        except OSError:
            self._send_json(500, {"error": "Dashboard asset could not be read."})
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global STREAM_KEY, STREAM_FORMAT

        if not self._is_local_request(require_origin=True):
            self._send_json(403, {"error": "Request origin is not allowed."})
            return

        path = urlsplit(self.path).path
        if path == "/api/stream/start":
            worker = getattr(self.server, "stream_worker", None)
            if worker is None:
                self._send_json(503, {"error": "Stream worker is unavailable."})
                return
            if not worker.request_start():
                self._send_json(409, {"error": "Save a YouTube stream key before starting the stream."})
                return
            self._send_json(202, {"state": "starting"})
            return
        if path == "/api/stream/stop":
            worker = getattr(self.server, "stream_worker", None)
            if worker is None:
                self._send_json(503, {"error": "Stream worker is unavailable."})
                return
            worker.request_stop()
            self._send_json(200, {"state": "idle"})
            return
        if path == "/api/preview":
            custom_video = os.environ.get("VIDEO_PATH", "").strip()
            stream_format = get_stream_format()
            if custom_video:
                self._send_json(
                    200,
                    {
                        "video_url": "/api/loop-video",
                        "format": stream_format,
                        "generated": False,
                    },
                )
                return

            try:
                design_seed = next_design_seed()
                design_id = f"{design_seed:016X}"
                width, height = video_dimensions(stream_format)
                preview_directory = Path(tempfile.gettempdir()) / "asif-tech-global-previews"
                video_path = preview_directory / f"{design_id}-{stream_format}.mp4"
                ffmpeg = find_ffmpeg()
                if ffmpeg is None:
                    self._send_json(503, {"error": "FFmpeg is unavailable; a new preview cannot be generated."})
                    return
                generate_loop_video(
                    video_path,
                    ffmpeg=ffmpeg,
                    seed=design_seed,
                    duration_seconds=12,
                    fps=8,
                    width=width,
                    height=height,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"ATG preview generation failed: {error}", flush=True)
                self._send_json(500, {"error": "A fresh ATG preview could not be generated. Check server logs."})
                return

            expired_paths = []
            with _PREVIEW_LOCK:
                _PREVIEW_VIDEOS[design_id] = video_path
                while len(_PREVIEW_VIDEOS) > PREVIEW_CACHE_SIZE:
                    _, expired_path = _PREVIEW_VIDEOS.popitem(last=False)
                    expired_paths.append(expired_path)
            for expired_path in expired_paths:
                try:
                    expired_path.unlink(missing_ok=True)
                except OSError as error:
                    print(f"Could not remove expired ATG preview {expired_path}: {error}", flush=True)
            self._send_json(
                200,
                {
                    "video_url": f"/api/loop-video?design={design_id}",
                    "design_id": design_id,
                    "design_number": design_seed,
                    "format": stream_format,
                    "generated": True,
                },
            )
            return
        if path == "/api/stream-format":
            payload = self._read_json()
            if payload is None:
                return
            stream_format = payload.get("format")
            try:
                video_dimensions(stream_format)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "Format must be horizontal or vertical."})
                return

            worker = getattr(self.server, "stream_worker", None)
            with _STREAM_CONFIG_LOCK:
                if worker is not None and worker.is_requested():
                    self._send_json(409, {"error": "Stop the live stream before changing its format."})
                    return
                STREAM_FORMAT = stream_format
            if worker is not None:
                worker.notify_config_changed()
            width, height = video_dimensions(stream_format)
            self._send_json(
                200,
                {"format": stream_format, "width": width, "height": height},
            )
            return
        if path != "/api/stream-key":
            self._send_json(404, {"error": "Not found."})
            return

        payload = self._read_json()
        if payload is None:
            return

        key = payload.get("key")
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_LENGTH:
            self._send_json(400, {"error": f"Key must contain 1 to {MAX_KEY_LENGTH} characters."})
            return

        STREAM_KEY = key.strip()
        worker = getattr(self.server, "stream_worker", None)
        if worker is not None:
            worker.notify_config_changed()
        self._send_json(200, {"configured": True})

    def do_DELETE(self):
        global STREAM_KEY

        if not self._is_local_request(require_origin=True):
            self._send_json(403, {"error": "Request origin is not allowed."})
            return
        if urlsplit(self.path).path != "/api/stream-key":
            self._send_json(404, {"error": "Not found."})
            return
        if STREAM_KEY is None:
            self._send_json(409, {"error": "There is no dashboard-saved stream key to remove."})
            return

        STREAM_KEY = None
        worker = getattr(self.server, "stream_worker", None)
        if worker is not None:
            worker.request_stop()
        configured = bool(os.environ.get("YOUTUBE_STREAM_KEY", "").strip())
        self._send_json(200, {"configured": configured})

    def log_message(self, format_string, *args):
        # Never log request bodies or submitted stream keys.
        super().log_message(format_string, *args)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    if host not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("HOST must be 127.0.0.1 or 0.0.0.0.")

    server = DashboardServer((host, port), DashboardHandler)
    server.stream_worker = StreamWorker()
    server.stream_worker.start()

    def request_shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    print(f"Asif Tech Global dashboard listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.", flush=True)
    finally:
        server.stream_worker.stop()
        server.server_close()


if __name__ == "__main__":
    main()

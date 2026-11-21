import asyncio
import json
import logging
import os
import re
import shutil
import time

from ambient_sampling import ContentBoxTracker, content_box, region_color, region_pixels
import device_tree
from run_as_user import user_env, user_cred

logger = logging.getLogger("colores.ambilight")

GAMESCOPE_NODE = "gamescope"
# Armada 20261010+ adds a bottom-screen gamescope that publishes a "gamescope" node too.
LEASE_CLIENT_FLAG = b"--drm-lease-client"

CAP_W = 32
CAP_H = 18

# Seconds between reconnect attempts when the gamescope source is missing or the
# stream drops. On a cold boot the user's PipeWire/gamescope session isn't ready when
# the (root) plugin loads, so the capture must keep retrying instead of giving up —
# otherwise ambient mode stays dark until the user manually re-selects it.
RETRY_INTERVAL = 3.0
# Each connect and disconnect makes gamescope reallocate its capture buffers, which some
# drivers crash on, so repeated drops back off.
MAX_RETRY_INTERVAL = 30.0
STABLE_STREAM_S = 60.0
HELPER_FPS = 30
HELPER_NO_LIBPIPEWIRE = 3
# kwin_capture.py reads the desktop through KWin's ScreenShot2 D-Bus API, which is
# the only source that exists outside Game Mode (there is no gamescope to tap).
KWIN_CAPTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kwin_capture.py")
KWIN_NO_SERVICE = 4
KWIN_NO_PYGOBJECT = 5
# Full-screen D-Bus captures are far heavier than a GPU-scaled gamescope frame, so the
# desktop path is capped instead of following the device render rate.
KWIN_MAX_FPS = 15
_GAMESCOPE_COMMS = (b"gamescope", b"steamcompmgr")

# Colour changes smaller than this (8-bit levels, per channel) are treated as sensor
# noise and ignored, so static screens never make the LEDs shimmer.
DEADBAND = 8
# Average per-channel frame delta at or below this means "nothing is moving".
IDLE_DELTA = 3
# After this many unchanged frames the capture rate drops to SLOW_CAPTURE_FPS; the
# helper keeps the same process so no reconnect/white flash happens.
IDLE_FRAMES_BEFORE_SLOW = 20
SLOW_CAPTURE_FPS = 3
# A helper that stops producing frames is treated as dead after this long.
CAPTURE_TIMEOUT = 10.0

_FULL_REGION = [0.0, 0.0, 1.0, 1.0]

# pw_capture.py on the host Python asks gamescope for a small GPU-scaled frame; GStreamer is
# the fallback when the host has no libpipewire.
PW_CAPTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pw_capture.py")
_PIPEWIRE_LIBS = (
    "usr/lib64/libpipewire-0.3.so.0",
    "usr/lib/libpipewire-0.3.so.0",
    "usr/lib/aarch64-linux-gnu/libpipewire-0.3.so.0",
    "usr/lib/x86_64-linux-gnu/libpipewire-0.3.so.0",
)


def host_capture_python(root="/"):
    for base in device_tree.host_roots(root):
        python = os.path.join(base, "usr/bin/python3")
        if os.path.exists(python) and any(
            os.path.exists(os.path.join(base, lib)) for lib in _PIPEWIRE_LIBS
        ):
            return python
    return None


def _helper_python():
    return host_capture_python()


def gamescope_running(proc_root="/proc"):
    """True when a gamescope compositor is up, i.e. the machine is in Game Mode."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "comm"), "rb") as handle:
                name = handle.read().strip()
        except OSError:
            continue
        if name in _GAMESCOPE_COMMS:
            return True
    return False


def capture_available():
    return shutil.which("gst-launch-1.0") is not None or _helper_python() is not None


_backend = None


def is_lease_companion(pid, proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as handle:
            return LEASE_CLIENT_FLAG in handle.read().split(b"\0")
    except OSError:
        return False


def _is_gamescope_video(props):
    return props.get("node.name") == GAMESCOPE_NODE and "Video" in str(props.get("media.class", ""))


_WPCTL_ID = re.compile(r"(\d+)\.\s")
_WPCTL_PROP = re.compile(r'^\s*\*?\s*([\w.-]+) = "(.*)"\s*$')


def wpctl_video_sources(status):
    ids = []
    section = subsection = None
    for line in status.splitlines():
        if line and not line[0].isspace():
            section = line.strip()
            continue
        stripped = line.strip(" │├└─\t")
        if stripped.endswith(":"):
            subsection = stripped
            continue
        if section == "Video" and subsection == "Sources:":
            match = _WPCTL_ID.search(line)
            if match:
                ids.append(match.group(1))
    return ids


def wpctl_props(text):
    return dict(match.groups() for match in map(_WPCTL_PROP.match, text.splitlines()) if match)


def subdivide(region, count):
    x0, y0, x1, y1 = region
    if count <= 1:
        return [tuple(region)]
    width = (x1 - x0) / count
    return [(x0 + i * width, y0, x0 + (i + 1) * width, y1) for i in range(count)]


def avg_region(frame, width, height, region):
    x0, y0, x1, y1 = region
    cx0 = max(0, int(x0 * width))
    cx1 = min(width, max(cx0 + 1, int(x1 * width)))
    cy0 = max(0, int(y0 * height))
    cy1 = min(height, max(cy0 + 1, int(y1 * height)))
    r = g = b = n = 0
    for y in range(cy0, cy1):
        base = y * width * 3
        for x in range(cx0, cx1):
            i = base + x * 3
            r += frame[i]
            g += frame[i + 1]
            b += frame[i + 2]
            n += 1
    if n == 0:
        return (0, 0, 0)
    return (r // n, g // n, b // n)


def boost_saturation(color, factor):
    r, g, b = color
    gray = r * 0.299 + g * 0.587 + b * 0.114
    return tuple(int(max(0, min(255, gray + (c - gray) * factor))) for c in (r, g, b))


def lerp(current, target, alpha):
    return tuple(int(c + (t - c) * alpha) for c, t in zip(current, target))


def alpha_for(smoothing):
    s = max(0, min(100, smoothing))
    return max(0.04, 1.0 - s / 100.0)


def _gst_command(node, width, height):
    caps = f"video/x-raw,format=RGB,width={width},height={height}"
    return [
        "gst-launch-1.0", "-q", "pipewiresrc", f"path={int(node)}",
        "!", "queue", "leaky=downstream", "max-size-buffers=2",
        "!", "videoconvert", "!", "videoscale", "!", caps, "!", "fdsink", "fd=1",
    ]


def _fall_back_to_gst():
    global _backend
    _backend = ("gst", None)
    logger.warning("ambilight capture helper cannot load libpipewire; using gst")
    return _backend


def _fall_back_to_pipewire():
    global _backend
    python = _helper_python()
    _backend = ("pipewire", python) if python else ("gst", None)
    logger.warning("ambilight desktop capture unavailable; using %s", _backend[0])
    return _backend


def _fall_back_to_kwin():
    global _backend
    python = _helper_python()
    _backend = ("kwin", python) if python else ("gst", None)
    logger.info("ambilight desktop capture available; using %s", _backend[0])
    return _backend


def retry_delay(drops):
    return min(MAX_RETRY_INTERVAL, RETRY_INTERVAL * (2 ** max(0, drops - 1)))


def _replace_with_latest(queue, item):
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


async def _read_latest_frames(reader, frame_bytes, queue):
    try:
        while True:
            frame = await reader.readexactly(frame_bytes)
            _replace_with_latest(queue, frame)
    except Exception as error:
        _replace_with_latest(queue, error)


class Ambilight:
    def __init__(self, apply_zones, zones, runtime_dir, uid=None, gid=None, layout=None, max_fps=None):
        self._apply = apply_zones
        self._zones = max(1, zones)
        self._runtime_dir = runtime_dir
        self._uid = uid
        self._gid = gid
        self._max_fps = max_fps
        self._layout = layout or [{"name": "Lights", "region": _FULL_REGION, "zones": list(range(self._zones))}]
        self._task = None
        self._proc = None
        self._options = {}
        self._kwin_failed = False
        self._previous_frame = None
        self._idle_frames = 0
        self._sent_fps = None
        self.status = "idle"
        self._current = [(0, 0, 0)] * self._zones
        self._targets = [(0, 0, 0)] * self._zones
        self._box_tracker = ContentBoxTracker()

    @property
    def running(self):
        return self._task is not None and not self._task.done()

    def _fallback(self):
        # Shown when there's no game source to sample (e.g. the Steam home screen, or a
        # cold boot before the session is up) so the LEDs hold the user's last solid color
        # instead of going dark. Routes through _apply, so brightness/power still apply.
        color = self._options.get("fallback") or (0, 0, 0)
        return [tuple(color)] * self._zones

    def _env(self):
        return user_env(self._runtime_dir)

    def _cred(self):
        return user_cred(self._uid, self._gid)

    async def _output(self, argv):
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._env(),
                **self._cred(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return out.decode(errors="replace")
        except (OSError, asyncio.TimeoutError):
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return ""

    async def _pipewire_target(self):
        status = await self._output(["wpctl", "status"])
        if not status:
            return GAMESCOPE_NODE
        for node in wpctl_video_sources(status):
            props = wpctl_props(await self._output(["wpctl", "inspect", node]))
            if not _is_gamescope_video(props) or not props.get("object.serial"):
                continue
            client = wpctl_props(await self._output(["wpctl", "inspect", props.get("client.id", "")]))
            if not is_lease_companion(client.get("application.process.id", "")):
                return props["object.serial"]
        return None

    async def _find_node(self):
        # Async so the retry loop never blocks the event loop while waiting on pw-dump
        # (it runs every RETRY_INTERVAL while the source is missing).
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "pw-dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._env(),
                **self._cred(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            data = json.loads(out)
        except (OSError, ValueError, asyncio.TimeoutError) as error:
            logger.warning("pw-dump failed: %s", error)
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return None
        for obj in data:
            props = (obj.get("info") or {}).get("props") or {}
            if _is_gamescope_video(props):
                return obj.get("id")
        return None

    async def _select_backend(self):
        global _backend
        if _backend is None:
            python = _helper_python()
            if python and not gamescope_running():
                _backend = ("kwin", python)
            elif python:
                _backend = ("pipewire", python)
            else:
                _backend = ("gst", None)
            logger.info("ambilight capture backend: %s", _backend[0])
        return _backend

    @staticmethod
    def layout_signature(layout):
        return tuple(
            (group.get("name"), tuple(group.get("region", ())), tuple(group.get("zones", ())))
            for group in (layout or ())
        )

    def start(self, options):
        options = options or {}
        layout = options.get("layout") or self._layout
        if self.running and self.layout_signature(layout) == self.layout_signature(self._layout):
            self._options = options
            return
        self.stop()
        self._options = options
        self._layout = [dict(group) for group in layout]
        self._box_tracker = ContentBoxTracker()
        self._task = asyncio.get_event_loop().create_task(self._run())

    def stop(self):
        self.status = "idle"
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._kill()

    async def stop_and_wait(self):
        task = self._task
        proc = self._proc
        self.stop()
        pending = [task] if task is not None else []
        if proc is not None:
            pending.append(proc.wait())
        await asyncio.gather(*pending, return_exceptions=True)

    def _kill(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            self._proc = None

    async def _run(self):
        # Outer reconnect loop: keep trying to find a screen source (gamescope in Game
        # Mode, KWin in the desktop) and capture it until stop() cancels us. The source
        # can be absent at boot or vanish when the session type changes, so both paths
        # are re-entered from here.
        frame_bytes = CAP_W * CAP_H * 3
        backend, python = await self._select_backend()
        drops = 0
        while True:
            if backend == "kwin":
                command = [
                    python, KWIN_CAPTURE,
                    str(CAP_W), str(CAP_H), str(self._kwin_fps()),
                ]
                node = "kwin"
            elif backend == "pipewire":
                node = await self._pipewire_target()
                if node is None:
                    if not gamescope_running():
                        # Left Game Mode for the desktop: gamescope is gone, so even the
                        # desktop source that failed earlier deserves another try.
                        self._kwin_failed = False
                        backend, python = _fall_back_to_kwin()
                        continue
                    logger.warning("main gamescope PipeWire node not found; retrying")
                    self.status = "no_source"
                    self._apply(self._fallback())
                    await asyncio.sleep(RETRY_INTERVAL)
                    continue
                command = [
                    python, PW_CAPTURE, node,
                    str(CAP_W), str(CAP_H), str(self._helper_fps()),
                ]
            else:
                node = await self._find_node()
                if node is None:
                    logger.warning("gamescope PipeWire node not found; retrying")
                    self.status = "no_source"
                    self._apply(self._fallback())
                    await asyncio.sleep(RETRY_INTERVAL)
                    continue
                command = _gst_command(node, CAP_W, CAP_H)
            proc = None
            reader_task = None
            stderr_task = None
            started = time.monotonic()
            logger.info("ambilight start: node=%s fps=%.0f", node, 1.0 / self._capture_interval())
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE if backend == "kwin" else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env(),
                    **self._cred(),
                )
                self._proc = proc
                self.status = "running"
                self._sent_fps = None
                self._idle_frames = 0
                self._previous_frame = None
                stderr_task = asyncio.create_task(self._drain_stderr(proc))
                await self._sync_capture_rate()
                frames = asyncio.Queue(maxsize=1)
                reader_task = asyncio.create_task(
                    _read_latest_frames(proc.stdout, frame_bytes, frames)
                )
                while True:
                    frame = await asyncio.wait_for(frames.get(), timeout=CAPTURE_TIMEOUT)
                    if isinstance(frame, Exception):
                        raise frame
                    self._update_targets(frame)
                    self._tick()
                    await self._sync_capture_rate()
                    await asyncio.sleep(self._capture_interval())
            except asyncio.TimeoutError:
                # The helper is alive but not producing frames: recycle it.
                self.status = "no_source"
                logger.warning("ambilight capture timed out; reconnecting")
                self._apply(self._fallback())
            except asyncio.IncompleteReadError:
                self.status = "no_source"
                await self._log_exit(proc)
                if backend == "pipewire" and proc.returncode == HELPER_NO_LIBPIPEWIRE:
                    backend, python = _fall_back_to_gst()
                elif backend == "kwin" and proc.returncode in (KWIN_NO_SERVICE, KWIN_NO_PYGOBJECT):
                    self._kwin_failed = True
                    backend, python = _fall_back_to_pipewire()
                self._apply(self._fallback())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ambilight loop failed")
            finally:
                if stderr_task is not None:
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                if reader_task is not None:
                    reader_task.cancel()
                    await asyncio.gather(reader_task, return_exceptions=True)
                if proc is not None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                if self._proc is proc:
                    self._proc = None
            drops = 0 if time.monotonic() - started >= STABLE_STREAM_S else drops + 1
            await asyncio.sleep(retry_delay(drops))

    async def _log_exit(self, proc):
        # stderr is drained continuously by _drain_stderr, so only the exit code is
        # reported here instead of reading a pipe that someone else owns.
        if proc is None:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        logger.warning("ambilight stream ended (rc=%s)", proc.returncode)

    def _sample(self, frame, box, region):
        return region_color(frame, CAP_W, *region_pixels(box, region))

    def _frame_delta(self, frame):
        previous = self._previous_frame
        self._previous_frame = frame
        if previous is None or len(previous) != len(frame):
            return None
        total = 0
        for old, new in zip(previous, frame):
            total += abs(old - new)
        return total // len(frame)

    def _deadband(self):
        try:
            return max(0, min(64, int(self._options.get("deadband", DEADBAND))))
        except (TypeError, ValueError):
            return DEADBAND

    def _settle(self, zone, color):
        # Hold the previous colour for changes below the deadband: capture noise on a
        # static screen would otherwise flicker one level at a time.
        deadband = self._deadband()
        if deadband <= 0:
            return color
        previous = self._targets[zone]
        if max(abs(new - old) for new, old in zip(color, previous)) < deadband:
            return previous
        return color

    def _update_targets(self, frame):
        delta = self._frame_delta(frame)
        if delta is not None and delta <= IDLE_DELTA:
            self._idle_frames += 1
        else:
            self._idle_frames = 0
        sat = float(self._options.get("saturation", 1.4))
        box = self._box_tracker.update(content_box(frame, CAP_W, CAP_H))
        if self._options.get("global_color"):
            target = boost_saturation(self._sample(frame, box, _FULL_REGION), sat)
            self._targets = [self._settle(zone, target) for zone in range(self._zones)]
            return
        bottom_edge = self._options.get("sampling") == "bottom_edge"
        for group in self._layout:
            indices = group["zones"]
            region = group["region"]
            if bottom_edge:
                x0, y0, x1, y1 = region
                region = (x0, y1 - (y1 - y0) * 0.28, x1, y1)
            for sub, zone in zip(subdivide(region, len(indices)), indices):
                if 0 <= zone < self._zones:
                    color = boost_saturation(self._sample(frame, box, sub), sat)
                    self._targets[zone] = self._settle(zone, color)

    def _kwin_fps(self):
        cap = KWIN_MAX_FPS if self._max_fps is None else min(KWIN_MAX_FPS, int(self._max_fps))
        return max(1, cap)

    def _capture_interval(self):
        fps = max(1, int(self._options.get("fps", 10)))
        if self._max_fps is not None:
            fps = min(fps, max(1, int(self._max_fps)))
        if self._idle_frames >= IDLE_FRAMES_BEFORE_SLOW:
            fps = min(fps, SLOW_CAPTURE_FPS)
        return 1.0 / fps

    async def _send_capture_rate(self, fps):
        # The desktop helper reads "fps N" from stdin, so the rate can follow the
        # scene without restarting the capture process.
        proc = self._proc
        stdin = getattr(proc, "stdin", None) if proc is not None else None
        if stdin is None:
            return
        try:
            stdin.write(f"fps {int(fps)}\n".encode())
            await stdin.drain()
            self._sent_fps = int(fps)
        except (BrokenPipeError, ConnectionResetError, OSError, AttributeError):
            pass

    async def _sync_capture_rate(self):
        want = self._kwin_fps()
        if self._idle_frames >= IDLE_FRAMES_BEFORE_SLOW:
            want = SLOW_CAPTURE_FPS
        if want != self._sent_fps:
            await self._send_capture_rate(want)

    async def _drain_stderr(self, proc):
        stderr = getattr(proc, "stderr", None)
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    return
                text = line.decode(errors="replace").strip()
                if text:
                    logger.info("ambilight capture: %s", text[:200])
        except (asyncio.CancelledError, ValueError, OSError):
            return

    def _helper_fps(self):
        # Fixed so option changes never reconnect; the loop throttles to the chosen fps.
        cap = HELPER_FPS if self._max_fps is None else min(HELPER_FPS, int(self._max_fps))
        return max(1, cap)

    def _tick(self):
        alpha = alpha_for(self._options.get("smoothing", 75))
        self._current = [lerp(c, t, alpha) for c, t in zip(self._current, self._targets)]
        self._apply(list(self._current))

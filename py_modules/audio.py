import array
import asyncio
import logging
import math
import time

from effects import frame_vu
from run_as_user import user_env, user_cred

logger = logging.getLogger("colores.audio")

_spawn = asyncio.create_subprocess_exec

RATE = 16000
CHUNK = 1024  # samples per frame (~64ms at 16kHz)
FULL_SCALE = 8000.0  # RMS mapped to level 1.0
DYNAMIC_RANGE_DB = 40.0
RETRY_INTERVAL = 3.0

# Ambilight + sound ("aurora"): the lights stay at the user's base brightness and
# flash to full on detected beats. The base keeps the screen colours visible in
# silence instead of dimming the whole case.
DEFAULT_BASE = 0.8
BASE_RANGE = (0.5, 1.0)
# Automatic gain: the peak follows the loudest chunk of the last few seconds, so a
# quiet track and a loud one produce the same amount of motion.
PEAK_DECAY = 0.998
PEAK_FLOOR = 0.12
# A beat is a jump above the moving average of the normalised level.
# Onset detector: the average chases a rising signal quickly (so one hit is one
# flash) and decays slowly (so the next hit stands out).
SLOW_RISE = 0.45
SLOW_FALL = 0.03
BEAT_RISE = 0.22
MIN_BEAT_INTERVAL = 0.15
PULSE_DECAY = 0.18


def _level_from_pcm(data):
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    if rms <= 0:
        return 0.0
    level = 1.0 + 20.0 * math.log10(rms / FULL_SCALE) / DYNAMIC_RANGE_DB
    return max(0.0, min(1.0, level))


def clamp_base(value):
    try:
        base = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BASE
    return max(BASE_RANGE[0], min(BASE_RANGE[1], base))


class AudioReactive:
    def __init__(self, apply_zones, zones, runtime_dir, uid=None, gid=None):
        self._apply = apply_zones
        self._zones = max(1, zones)
        self._runtime_dir = runtime_dir
        self._uid = uid
        self._gid = gid
        self._task = None
        self._proc = None
        self._mode = "vu"
        self._base = DEFAULT_BASE
        self._meter = 0.0
        self._display = 0.0
        self._peak = 0.0
        self._slow = 0.0
        self._pulse = 0.0
        self._last_beat = 0.0
        self._last_chunk = None
        self.status = "idle"

    @property
    def running(self):
        return self._task is not None and not self._task.done()

    @property
    def level(self):
        """Brightness factor used by the ambilight aurora mode (base .. 1.0)."""
        return self._display

    @property
    def meter(self):
        """Normalised loudness used by the VU meter mode (0 .. 1)."""
        return self._meter

    def _env(self):
        return user_env(self._runtime_dir)

    def _cred(self):
        return user_cred(self._uid, self._gid)

    def start(self, options=None):
        options = options or {}
        mode = options.get("mode", "vu")
        base = clamp_base(options.get("base", DEFAULT_BASE))
        if self.running and mode == self._mode and base == self._base:
            return
        self.stop()
        self._mode = mode
        self._base = base
        self._task = asyncio.get_event_loop().create_task(self._run())

    def stop(self):
        self.status = "idle"
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._kill()
        self._meter = 0.0
        self._display = 0.0
        self._pulse = 0.0
        self._slow = 0.0
        self._last_chunk = None

    def _kill(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            self._proc = None

    def _process(self, raw, now):
        # Normalise against a slowly decaying peak so any source can push the meter
        # to the top without hard-coding a volume.
        self._peak = max(raw, self._peak * PEAK_DECAY)
        gain = 1.0 if self._peak <= 0 else 1.0 / max(self._peak, PEAK_FLOOR)
        normalised = max(0.0, min(1.0, raw * gain))
        self._meter = normalised

        if self._last_chunk is None:
            # Prime the moving average: the first chunk is never a beat, otherwise
            # starting the capture would flash the whole case.
            self._slow = normalised
            self._last_chunk = now
            self._display = self._base
            return normalised

        elapsed = max(0.0, now - self._last_chunk)
        self._pulse = max(0.0, self._pulse - elapsed / PULSE_DECAY)
        self._last_chunk = now

        alpha = SLOW_RISE if normalised > self._slow else SLOW_FALL
        self._slow += (normalised - self._slow) * alpha
        if normalised - self._slow >= BEAT_RISE and now - self._last_beat >= MIN_BEAT_INTERVAL:
            self._last_beat = now
            self._pulse = 1.0

        self._display = self._base + (1.0 - self._base) * self._pulse
        return normalised

    def _emit(self, normalised):
        if self._mode == "vu":
            self._apply(frame_vu(normalised, self._zones))

    async def _run(self):
        command = [
            "parec", "--format=s16le", f"--rate={RATE}", "--channels=1",
            "--device=@DEFAULT_MONITOR@", "--raw",
        ]
        while True:
            proc = None
            try:
                proc = await _spawn(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env(),
                    **self._cred(),
                )
                self._proc = proc
                self.status = "running"
                while True:
                    data = await proc.stdout.readexactly(CHUNK * 2)
                    normalised = self._process(_level_from_pcm(data), time.monotonic())
                    self._emit(normalised)
            except asyncio.IncompleteReadError:
                self.status = "no_source"
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = "no_source"
                logger.exception("audio loop failed")
            finally:
                if proc is not None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                if self._proc is proc:
                    self._proc = None
            await asyncio.sleep(RETRY_INTERVAL)

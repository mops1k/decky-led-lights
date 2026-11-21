"""Standalone desktop capture for the KDE Wayland session (KWin ScreenShot2).

Same stdout contract as pw_capture.py: prints raw RGB frames of WIDTH x HEIGHT at
the requested FPS, so the ambilight loop can treat both sources alike. Runs on the
host Python as the session user (needs the session D-Bus address).

Usage: kwin_capture.py WIDTH HEIGHT FPS
Exit codes: 0 stdout closed, 2 capture lost, 4 KWin ScreenShot2 unavailable,
5 helper dependencies missing (no Gio).
"""
import os
import sys
import time

Gio = GLib = None
_IMPORT_ERROR = None
try:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
except Exception as error:  # pragma: no cover - only without pygobject
    _IMPORT_ERROR = error

BUS_NAME = "org.kde.KWin"
OBJECT_PATH = "/org/kde/KWin/ScreenShot2"
INTERFACE = "org.kde.KWin.ScreenShot2"
CALL_TIMEOUT_MS = 4000

# QImage::Format -> byte order of (r, g, b) inside a 4-byte little-endian pixel.
PIXEL_LAYOUTS = {
    4: (2, 1, 0),   # Format_RGB32
    5: (2, 1, 0),   # Format_ARGB32
    6: (2, 1, 0),   # Format_ARGB32_Premultiplied
    16: (2, 1, 0),  # Format_RGBX8888 / ARGB32 as delivered by KWin (BGRA bytes)
    17: (0, 1, 2),  # Format_RGBA8888
}

EXIT_OK = 0
EXIT_LOST = 2
EXIT_NO_KWIN = 4
EXIT_NO_PYGOBJECT = 5

# Pixels skipped when averaging a colour block: the block still averages what the
# user sees, at a fraction of the cost of touching every pixel.
SAMPLE_STEP = 3


class CaptureError(Exception):
    pass


class KwinCapture:
    def __init__(self, width, height):
        self.width = width
        self.height = height

    def _connection(self):
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(self, conn, read_fd, write_fd):
        fd_list = Gio.UnixFDList.new()
        fd_list.append(write_fd)
        params = GLib.Variant(
            "(a{sv}h)",
            ({"include-cursor": GLib.Variant("b", False)}, 0),
        )
        try:
            reply, _ = conn.call_with_unix_fd_list_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "CaptureActiveScreen",
                params,
                None,
                Gio.DBusCallFlags.NONE,
                CALL_TIMEOUT_MS,
                fd_list,
                None,
            )
        finally:
            os.close(write_fd)
        meta = reply.unpack()[0]
        kind = meta.get("type", "raw")
        if kind != "raw":
            raise CaptureError("unsupported capture type %r" % (kind,))
        return (
            int(meta.get("width", 0)),
            int(meta.get("height", 0)),
            int(meta.get("stride", 0)),
            int(meta.get("format", 4)),
        )

    def frame(self, conn):
        read_fd, write_fd = os.pipe()
        try:
            width, height, stride, fmt = self._call(conn, read_fd, write_fd)
            expected = stride * height
            data = bytearray()
            while len(data) < expected:
                chunk = os.read(read_fd, expected - len(data))
                if not chunk:
                    raise CaptureError("short frame from KWin")
                data += chunk
        finally:
            os.close(read_fd)
        if width <= 0 or height <= 0:
            raise CaptureError("empty frame")
        return bytes(data), width, height, stride, fmt


def _numpy_source():
    try:
        import numpy  # noqa: F401

        return numpy
    except Exception:
        return None


def downsample_numpy(np, data, width, height, stride, order, out_w, out_h):
    pixels = np.frombuffer(data, dtype=np.uint8, count=stride * height)
    image = pixels.reshape(height, stride // 4, 4)[:, :width, :]
    rgb = image[:, :, list(order)]
    sampled = rgb[::SAMPLE_STEP, ::SAMPLE_STEP, :]
    h, w = sampled.shape[0], sampled.shape[1]
    y_edges = (np.arange(out_h + 1) * h) // out_h
    x_edges = (np.arange(out_w + 1) * w) // out_w
    totals = np.add.reduceat(
        np.add.reduceat(sampled.astype(np.uint32), y_edges[:-1], axis=0),
        x_edges[:-1],
        axis=1,
    )
    counts = np.outer(np.diff(y_edges), np.diff(x_edges))[:, :, None]
    counts = np.maximum(counts, 1)
    return (totals // counts).astype(np.uint8).tobytes()


def downsample_python(data, width, height, stride, order, out_w, out_h):
    frame = bytearray(out_w * out_h * 3)
    for oy in range(out_h):
        y0 = (oy * height) // out_h
        y1 = max(y0 + 1, ((oy + 1) * height) // out_h)
        for ox in range(out_w):
            x0 = (ox * width) // out_w
            x1 = max(x0 + 1, ((ox + 1) * width) // out_w)
            r = g = b = n = 0
            for y in range(y0, y1, SAMPLE_STEP):
                base = y * stride
                for x in range(x0, x1, SAMPLE_STEP):
                    i = base + x * 4
                    r += data[i + order[0]]
                    g += data[i + order[1]]
                    b += data[i + order[2]]
                    n += 1
            out = (oy * out_w + ox) * 3
            if n:
                frame[out] = r // n
                frame[out + 1] = g // n
                frame[out + 2] = b // n
    return bytes(frame)


def build_downsampler(out_w, out_h):
    np = _numpy_source()
    if np is not None:
        return lambda data, width, height, stride, order: downsample_numpy(
            np, data, width, height, stride, order, out_w, out_h
        )
    return lambda data, width, height, stride, order: downsample_python(
        data, width, height, stride, order, out_w, out_h
    )


def kwin_available():
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        conn.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            "org.freedesktop.DBus.Peer",
            "Ping",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        return True
    except Exception:
        return False


def main(argv):
    if Gio is None:
        sys.stderr.write("kwin_capture: pygobject unavailable: %s\n" % (_IMPORT_ERROR,))
        return EXIT_NO_PYGOBJECT
    if len(argv) != 4:
        sys.stderr.write("usage: kwin_capture.py WIDTH HEIGHT FPS\n")
        return EXIT_LOST
    width, height, fps = (int(value) for value in argv[1:])
    fps = max(1, fps)
    interval = 1.0 / fps
    capture = KwinCapture(width, height)
    downsample = build_downsampler(width, height)
    try:
        conn = capture._connection()
    except Exception as error:
        sys.stderr.write("kwin_capture: session bus unavailable: %s\n" % (error,))
        return EXIT_NO_KWIN
    out = sys.stdout.buffer
    fails = 0
    while True:
        started = time.monotonic()
        try:
            data, src_w, src_h, stride, fmt = capture.frame(conn)
            order = PIXEL_LAYOUTS.get(fmt, (2, 1, 0))
            frame = downsample(data, src_w, src_h, stride, order)
        except CaptureError as error:
            fails += 1
            sys.stderr.write("kwin_capture: %s\n" % (error,))
            if fails >= 3:
                return EXIT_LOST
            time.sleep(0.5)
            continue
        except (GLib.Error, OSError) as error:
            sys.stderr.write("kwin_capture: kwin unavailable: %s\n" % (error,))
            return EXIT_NO_KWIN
        fails = 0
        try:
            out.write(frame)
            out.flush()
        except (BrokenPipeError, OSError):
            return EXIT_OK
        elapsed = time.monotonic() - started
        if elapsed < interval:
            time.sleep(interval - elapsed)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

BAR_LEVEL = 24
BAR_SHARE_PERCENT = 90
MAX_CROP_PERCENT = 30
CHROMA_WEIGHT = 4
ADOPT_FRAMES = 3
RELEASE_FRAMES = 20


def _peak(frame, index):
    return max(frame[index], frame[index + 1], frame[index + 2])


def _is_bar(frame, indices):
    dark = sum(1 for index in indices if _peak(frame, index) <= BAR_LEVEL)
    return dark * 100 >= len(indices) * BAR_SHARE_PERCENT


def content_box(frame, width, height):
    def row(y):
        return [(y * width + x) * 3 for x in range(width)]

    def column(x, y0, y1):
        return [(y * width + x) * 3 for y in range(y0, y1)]

    if all(_is_bar(frame, row(y)) for y in range(height)):
        return (0, 0, width, height)
    top, bottom = _symmetric_bars(lambda y: _is_bar(frame, row(y)), height)
    left, right = _symmetric_bars(lambda x: _is_bar(frame, column(x, top, bottom)), width)
    return (left, top, right, bottom)


def _symmetric_bars(is_bar, size):
    limit = size * MAX_CROP_PERCENT // 100
    start = 0
    while start < limit and is_bar(start):
        start += 1
    end = 0
    while end < limit and is_bar(size - 1 - end):
        end += 1
    if start == 0 or end == 0 or abs(start - end) > 1:
        return 0, size
    return start, size - end


def region_color(frame, width, x0, y0, x1, y1):
    plain = [0, 0, 0]
    weighted = [0, 0, 0]
    count = 0
    total_weight = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            index = (y * width + x) * 3
            pixel = (frame[index], frame[index + 1], frame[index + 2])
            weight = 255 + CHROMA_WEIGHT * (max(pixel) - min(pixel))
            for channel in range(3):
                plain[channel] += pixel[channel]
                weighted[channel] += pixel[channel] * weight
            count += 1
            total_weight += weight
    if count == 0:
        return (0, 0, 0)
    mean_peak = max(value // count for value in plain)
    color = [value // total_weight for value in weighted]
    color_peak = max(color)
    if color_peak == 0:
        return (0, 0, 0)
    return tuple(min(255, (value * mean_peak + color_peak // 2) // color_peak) for value in color)


EDGE_BAND = 0.10
EDGE_MIN_BAND = 0.04
EDGE_MAX_BAND = 0.30
EDGE_ABXY_POSITIONS = ("bottom", "top", "center")
# Screen border slices for the four LEDs around a stick ring, top to bottom.
_EDGE_SLICES = ((0.02, 0.26), (0.26, 0.50), (0.50, 0.74), (0.74, 0.98))


def clamp_band(value, default=EDGE_BAND):
    try:
        band = float(value)
    except (TypeError, ValueError):
        return default
    return max(EDGE_MIN_BAND, min(EDGE_MAX_BAND, band))


def edge_layout(zones, band=EDGE_BAND, reverse=False, abxy="bottom"):
    """Edge ambilight layout: every LED reads the screen border next to it.

    The MSI Claw family has nine zones: 0-3 around the right stick, 4-7 around the
    left stick and 8 on the ABXY buttons, so the ring LEDs map to the matching side
    of the screen and the button zone reads one edge. ``reverse`` flips the ring
    slices (bottom-up instead of top-down) and ``abxy`` picks the edge for the
    button zone. Any other zone count falls back to an even ring of the borders.
    """
    zones = int(zones)
    if zones <= 0:
        return []
    band = clamp_band(band)
    abxy = abxy if abxy in EDGE_ABXY_POSITIONS else "bottom"
    if zones == 9:
        slices = list(_EDGE_SLICES)
        if reverse:
            slices = list(reversed(slices))
        layout = []
        for index, (y0, y1) in enumerate(slices):
            layout.append(
                {
                    "name": "Left stick",
                    "region": [0.0, y0, band, y1],
                    "zones": [4 + index],
                }
            )
        for index, (y0, y1) in enumerate(slices):
            layout.append(
                {
                    "name": "Right stick",
                    "region": [1.0 - band, y0, 1.0, y1],
                    "zones": [index],
                }
            )
        layout.append(
            {
                "name": "Lights",
                "region": _abxy_region(band, abxy),
                "zones": [8],
            }
        )
        return layout
    return [
        {"name": "Lights", "region": list(region), "zones": [index]}
        for index, region in enumerate(_perimeter_regions(zones, band))
    ]


def _abxy_region(band, abxy):
    if abxy == "top":
        return [0.30, 0.0, 0.70, band]
    if abxy == "center":
        return [0.30, 0.42, 0.70, 0.58]
    return [0.30, 1.0 - band, 0.70, 1.0]


def _perimeter_slices(count, reverse=False):
    slices = _EDGE_SLICES if count == 4 else None
    if slices is None:
        step = 1.0 / max(1, count)
        slices = tuple((index * step, (index + 1) * step) for index in range(count))
    return list(reversed(slices)) if reverse else list(slices)


def _perimeter_regions(count, band=EDGE_BAND):
    # Even ring: the borders are split into four runs and every zone reads a slice
    # of the run it lands on, starting at the top edge and going clockwise.
    shares = []
    remaining = count
    for index in range(4):
        share = remaining // (4 - index)
        shares.append(share)
        remaining -= share
    sides = ("top", "right", "bottom", "left")
    regions = []
    for side, share in zip(sides, shares):
        for start, end in _split(share):
            if side == "top":
                regions.append([start, 0.0, end, band])
            elif side == "right":
                regions.append([1.0 - band, start, 1.0, end])
            elif side == "bottom":
                regions.append([start, 1.0 - band, end, 1.0])
            else:
                regions.append([0.0, start, band, end])
    return regions


def _split(count, lo=0.0, hi=1.0):
    if count <= 0:
        return []
    step = (hi - lo) / count
    return [(lo + index * step, lo + (index + 1) * step) for index in range(count)]


def region_pixels(box, region):
    left, top, right, bottom = box
    x0, y0, x1, y1 = region
    width = right - left
    height = bottom - top
    px0 = left + int(x0 * width)
    py0 = top + int(y0 * height)
    px1 = max(px0 + 1, min(right, left + int(x1 * width)))
    py1 = max(py0 + 1, min(bottom, top + int(y1 * height)))
    return (min(px0, right - 1), min(py0, bottom - 1), px1, py1)


class ContentBoxTracker:
    def __init__(self):
        self.box = None
        self._candidate = None
        self._streak = 0
        self._growth = 0

    def update(self, detected):
        if self.box is None:
            self.box = detected
        if detected == self.box:
            self._candidate = None
            self._streak = 0
            self._growth = 0
            return self.box
        if _area(detected) > _area(self.box):
            self._candidate = None
            self._streak = 0
            self._growth += 1
            if self._growth >= RELEASE_FRAMES:
                self.box = detected
                self._growth = 0
            return self.box
        self._growth = 0
        if detected != self._candidate:
            self._candidate = detected
            self._streak = 0
        self._streak += 1
        if self._streak >= ADOPT_FRAMES:
            self.box = detected
            self._candidate = None
            self._streak = 0
        return self.box


def _area(box):
    return (box[2] - box[0]) * (box[3] - box[1])

import kwin_capture as capture


def _bgra_frame(width, height, color):
    b, g, r = color[2], color[1], color[0]
    row = bytes((b, g, r, 255)) * width
    return row * height


def test_python_downsampler_reads_bgra_bytes_in_rgb_order():
    frame = _bgra_frame(8, 8, (10, 20, 30))
    out = capture.downsample_python(frame, 8, 8, 8 * 4, capture.PIXEL_LAYOUTS[16], 2, 2)
    assert out == bytes((10, 20, 30)) * 4


def test_python_downsampler_keeps_borders_distinct():
    top = _bgra_frame(8, 4, (255, 0, 0))
    bottom = _bgra_frame(8, 4, (0, 0, 255))
    out = capture.downsample_python(top + bottom, 8, 8, 8 * 4, capture.PIXEL_LAYOUTS[16], 1, 2)
    assert out[:3] == bytes((255, 0, 0))
    assert out[3:] == bytes((0, 0, 255))


def test_unknown_formats_fall_back_to_bgra_order():
    assert capture.PIXEL_LAYOUTS.get(999) is None


def test_built_downsampler_accepts_kwin_frame_layout():
    frame = _bgra_frame(6, 3, (1, 2, 3))
    downsample = capture.build_downsampler(3, 2)
    out = downsample(frame, 6, 3, 6 * 4, capture.PIXEL_LAYOUTS[16])
    assert len(out) == 3 * 2 * 3
    assert set(out) == {1, 2, 3}


def test_rate_commands_from_stdin_are_parsed():
    assert capture.parse_rate_command("fps 3\n", 15) == 3
    assert capture.parse_rate_command("fps 120", 15) == 60
    assert capture.parse_rate_command("fps 0", 15) == 1
    assert capture.parse_rate_command("nonsense", 15) == 15
    assert capture.parse_rate_command("", 15) == 15


def test_rate_reader_stops_after_end_of_stream():
    import os

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"fps 5\n")
    os.close(write_fd)
    stream = os.fdopen(read_fd)
    try:
        reader = capture.RateReader(stream)
        assert reader.poll(15) == 5
        assert reader.poll(15) == 15
    finally:
        stream.close()

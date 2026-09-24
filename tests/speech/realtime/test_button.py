from msb_v3.speech.realtime.button import PLAY_PAUSE_KEYCODE, parse_media_key


def test_parse_play_pause_down():
    data1 = (PLAY_PAUSE_KEYCODE << 16) | (0xA << 8)
    assert parse_media_key(data1) == (16, True)


def test_parse_play_pause_up():
    data1 = (PLAY_PAUSE_KEYCODE << 16) | (0xB << 8)
    assert parse_media_key(data1) == (16, False)

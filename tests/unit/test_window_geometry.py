import pytest
from ui.window_geometry import Bounds, fit_to_screens


@pytest.mark.parametrize("requested,screens,expected", [
    (Bounds(40, 40, 660, 130), (Bounds(0, 0, 1920, 1040),), Bounds(40, 40, 660, 130)),
    (Bounds(-1800, 200, 660, 130), (Bounds(-1920, 0, 1920, 1080), Bounds(0, 0, 1920, 1040)), Bounds(-1800, 200, 660, 130)),
    (Bounds(99999, 99999, 660, 130), (Bounds(0, 0, 1920, 1040),), Bounds(1260, 910, 660, 130)),
    (Bounds(-2000, 50, 660, 130), (Bounds(0, 0, 1920, 1040),), Bounds(0, 50, 660, 130)),
    (Bounds(300, 100, 4000, 2200), (Bounds(0, 0, 1920, 1040),), Bounds(0, 0, 1920, 1040)),
    (Bounds(1300, 100, 660, 130), (Bounds(0, 0, 1280, 720), Bounds(1600, 0, 1280, 720)), Bounds(1600, 100, 660, 130)),
])
def test_overlay_restoration_in_virtual_monitor_layout(requested, screens, expected):
    assert fit_to_screens(requested, screens) == expected


def test_missing_monitor_layout_is_rejected():
    with pytest.raises(ValueError):
        fit_to_screens(Bounds(0, 0, 660, 130), ())

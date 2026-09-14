"""Monitor-aware overlay geometry in Qt logical pixels, including negative origins."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int


def fit_to_screens(requested: Bounds, screens: tuple[Bounds, ...]) -> Bounds:
    if not screens or any(screen.width <= 0 or screen.height <= 0 for screen in screens):
        raise ValueError("At least one usable screen is required")
    def intersection(screen):
        width = max(0, min(requested.x + requested.width, screen.x + screen.width) - max(requested.x, screen.x))
        height = max(0, min(requested.y + requested.height, screen.y + screen.height) - max(requested.y, screen.y))
        return width * height
    def distance(screen):
        cx, cy = requested.x + requested.width / 2, requested.y + requested.height / 2
        return max(screen.x - cx, 0, cx - screen.x - screen.width) ** 2 + max(screen.y - cy, 0, cy - screen.y - screen.height) ** 2
    screen = max(screens, key=lambda item: (intersection(item), -distance(item)))
    width, height = min(max(1, requested.width), screen.width), min(max(1, requested.height), screen.height)
    return Bounds(min(max(requested.x, screen.x), screen.x + screen.width - width),
                  min(max(requested.y, screen.y), screen.y + screen.height - height), width, height)

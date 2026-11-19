"""Plain geometry shared by layout, routing and the geometry checks."""

from __future__ import annotations

from dataclasses import dataclass

GRID = 20  # every coordinate cwc writes is a multiple of this
TITLE_HEIGHT = 30  # LiteGraph NODE_TITLE_HEIGHT; the title bar is drawn above pos


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    def overlaps(self, other: Rect) -> bool:
        """True when the interiors intersect; touching edges do not count."""
        return self.x < other.right and other.x < self.right and self.y < other.bottom and other.y < self.bottom

    def contains_point(self, px: float, py: float) -> bool:
        return self.x <= px <= self.right and self.y <= py <= self.bottom

    def contains_rect(self, other: Rect) -> bool:
        return self.x <= other.x and self.y <= other.y and other.right <= self.right and other.bottom <= self.bottom

    def union(self, other: Rect) -> Rect:
        x = min(self.x, other.x)
        y = min(self.y, other.y)
        return Rect(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)

    def inflate(self, left: float, top: float, right: float, bottom: float) -> Rect:
        return Rect(self.x - left, self.y - top, self.w + left + right, self.h + top + bottom)

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.w, self.h]


def snap_up(value: float, grid: int = GRID) -> int:
    """Smallest multiple of ``grid`` that is >= value."""
    return int(-(-value // grid) * grid)


def snap_down(value: float, grid: int = GRID) -> int:
    return int(value // grid * grid)


def bounding(rects: list[Rect]) -> Rect:
    if not rects:
        raise ValueError("bounding() of no rectangles")
    out = rects[0]
    for rect in rects[1:]:
        out = out.union(rect)
    return out

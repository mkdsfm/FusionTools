from dataclasses import dataclass
import math


MAX_GRID_CELLS = 100_000
MAX_SKETCH_LINES = 50_000


@dataclass(frozen=True)
class Segment:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class ColorGeometry:
    color_index: int
    rgb: tuple[int, int, int]
    segments: tuple[Segment, ...]


def calculate_grid(width_mm: float, height_mm: float, pixel_size_mm: float) -> tuple[int, int]:
    if width_mm <= 0 or height_mm <= 0 or pixel_size_mm <= 0:
        raise ValueError('Width, height, and pixel size must be greater than zero.')

    columns_ratio = width_mm / pixel_size_mm
    rows_ratio = height_mm / pixel_size_mm
    columns = round(columns_ratio)
    rows = round(rows_ratio)

    if columns < 1 or not math.isclose(columns_ratio, columns, rel_tol=0, abs_tol=1e-7):
        raise ValueError('Width must be exactly divisible by the pixel size.')
    if rows < 1 or not math.isclose(rows_ratio, rows, rel_tol=0, abs_tol=1e-7):
        raise ValueError('Height must be exactly divisible by the pixel size.')
    if columns * rows > MAX_GRID_CELLS:
        raise ValueError(f'The grid is limited to {MAX_GRID_CELLS:,} pixels.')

    return columns, rows


def build_color_geometry(pixels: list[list[int]], palette: list[tuple[int, int, int]]) -> list[ColorGeometry]:
    if not pixels or not pixels[0]:
        raise ValueError('The pixel matrix is empty.')

    columns = len(pixels[0])
    if any(len(row) != columns for row in pixels):
        raise ValueError('All pixel rows must have the same width.')

    rows = len(pixels)
    result = []

    for color_index, rgb in enumerate(palette):
        horizontal: dict[int, list[tuple[int, int]]] = {}
        vertical: dict[int, list[tuple[int, int]]] = {}

        def add_horizontal(y: int, x1: int, x2: int):
            horizontal.setdefault(y, []).append((x1, x2))

        def add_vertical(x: int, y1: int, y2: int):
            vertical.setdefault(x, []).append((y1, y2))

        for row in range(rows):
            for column in range(columns):
                if pixels[row][column] != color_index:
                    continue

                # Image row zero is the top. Sketch Y grows upward.
                x1 = column
                x2 = column + 1
                y1 = rows - row - 1
                y2 = rows - row

                if row == 0 or pixels[row - 1][column] != color_index:
                    add_horizontal(y2, x1, x2)
                if row == rows - 1 or pixels[row + 1][column] != color_index:
                    add_horizontal(y1, x1, x2)
                if column == 0 or pixels[row][column - 1] != color_index:
                    add_vertical(x1, y1, y2)
                if column == columns - 1 or pixels[row][column + 1] != color_index:
                    add_vertical(x2, y1, y2)

        segments = []
        for y, intervals in horizontal.items():
            segments.extend(Segment(x1, y, x2, y) for x1, x2 in _merge_intervals(intervals))
        for x, intervals in vertical.items():
            segments.extend(Segment(x, y1, x, y2) for y1, y2 in _merge_intervals(intervals))

        if segments:
            result.append(ColorGeometry(color_index, rgb, tuple(segments)))

    line_count = sum(len(item.segments) for item in result)
    if line_count > MAX_SKETCH_LINES:
        raise ValueError(
            f'The image would create {line_count:,} sketch lines. '
            f'Increase the pixel size or reduce image detail; the limit is {MAX_SKETCH_LINES:,}.'
        )

    return result


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []

    sorted_intervals = sorted(intervals)
    result = []
    start, end = sorted_intervals[0]

    for next_start, next_end in sorted_intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            result.append((start, end))
            start, end = next_start, next_end

    result.append((start, end))
    return result

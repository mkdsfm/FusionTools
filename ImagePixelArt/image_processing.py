from dataclasses import dataclass
from collections import deque

from png_reader import read_png


@dataclass(frozen=True)
class PixelImage:
    pixels: list[list[int]]
    palette: list[tuple[int, int, int]]


def load_and_quantize(path: str, columns: int, rows: int, color_count: int, remove_small_regions: bool = True) -> PixelImage:
    source = read_png(path)
    resized = _resize_bilinear(source.pixels, source.width, source.height, columns, rows)
    palette, lab_palette = _median_cut_palette(resized, color_count)
    color_map = {
        color: _nearest_lab_color(_rgb_to_lab(color), lab_palette)
        for color in set(resized)
    }
    color_indexes = [color_map[pixel] for pixel in resized]
    pixels = [color_indexes[row * columns:(row + 1) * columns] for row in range(rows)]
    if remove_small_regions:
        pixels = replace_small_regions(pixels, palette)
    return PixelImage(pixels, palette)


def replace_small_regions(pixels: list[list[int]], palette: list[tuple[int, int, int]], max_size: int = 2) -> list[list[int]]:
    if not pixels or not pixels[0] or max_size < 1:
        return [row[:] for row in pixels]

    rows = len(pixels)
    columns = len(pixels[0])
    if any(len(row) != columns for row in pixels):
        raise ValueError('All pixel rows must have the same width.')

    labels = [[-1] * columns for _ in range(rows)]
    components = []

    for row in range(rows):
        for column in range(columns):
            if labels[row][column] != -1:
                continue

            component_id = len(components)
            color_index = pixels[row][column]
            cells = []
            queue = deque(((row, column),))
            labels[row][column] = component_id

            while queue:
                current_row, current_column = queue.popleft()
                cells.append((current_row, current_column))

                for neighbor_row, neighbor_column in _neighbors(current_row, current_column, rows, columns):
                    if labels[neighbor_row][neighbor_column] == -1 and pixels[neighbor_row][neighbor_column] == color_index:
                        labels[neighbor_row][neighbor_column] = component_id
                        queue.append((neighbor_row, neighbor_column))

            components.append((color_index, cells))

    result = [row[:] for row in pixels]
    component_sizes = [len(cells) for _, cells in components]

    for component_id, (source_color, cells) in enumerate(components):
        if len(cells) > max_size:
            continue

        for row, column in cells:
            contacts = {}
            largest_regions = {}

            for neighbor_row, neighbor_column in _neighbors(row, column, rows, columns):
                neighbor_component = labels[neighbor_row][neighbor_column]
                if neighbor_component == component_id or component_sizes[neighbor_component] <= max_size:
                    continue

                neighbor_color = pixels[neighbor_row][neighbor_column]
                contacts[neighbor_color] = contacts.get(neighbor_color, 0) + 1
                largest_regions[neighbor_color] = max(
                    largest_regions.get(neighbor_color, 0),
                    component_sizes[neighbor_component]
                )

            if contacts:
                result[row][column] = max(
                    contacts,
                    key=lambda color: (
                        contacts[color],
                        largest_regions[color],
                        -_color_distance(palette[source_color], palette[color]),
                        -color
                    )
                )
    return result


def _neighbors(row: int, column: int, rows: int, columns: int):
    if row > 0:
        yield row - 1, column
    if row + 1 < rows:
        yield row + 1, column
    if column > 0:
        yield row, column - 1
    if column + 1 < columns:
        yield row, column + 1


def _color_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> int:
    return sum((first[channel] - second[channel]) ** 2 for channel in range(3))


def _resize_bilinear(pixels, source_width: int, source_height: int, target_width: int, target_height: int):
    if source_width == target_width and source_height == target_height:
        return list(pixels)

    result = []
    for target_y in range(target_height):
        source_y = (target_y + 0.5) * source_height / target_height - 0.5
        y1 = max(0, min(source_height - 1, int(source_y)))
        y2 = min(source_height - 1, y1 + 1)
        y_fraction = max(0.0, source_y - y1)

        for target_x in range(target_width):
            source_x = (target_x + 0.5) * source_width / target_width - 0.5
            x1 = max(0, min(source_width - 1, int(source_x)))
            x2 = min(source_width - 1, x1 + 1)
            x_fraction = max(0.0, source_x - x1)

            top_left = pixels[y1 * source_width + x1]
            top_right = pixels[y1 * source_width + x2]
            bottom_left = pixels[y2 * source_width + x1]
            bottom_right = pixels[y2 * source_width + x2]
            result.append(tuple(
                round(
                    top_left[channel] * (1 - x_fraction) * (1 - y_fraction)
                    + top_right[channel] * x_fraction * (1 - y_fraction)
                    + bottom_left[channel] * (1 - x_fraction) * y_fraction
                    + bottom_right[channel] * x_fraction * y_fraction
                )
                for channel in range(3)
            ))
    return result


def _median_cut_palette(pixels, color_count: int):
    histogram = {}
    for pixel in pixels:
        histogram[pixel] = histogram.get(pixel, 0) + 1
    lab_colors = {color: _rgb_to_lab(color) for color in histogram}

    boxes = [histogram]
    while len(boxes) < color_count:
        splittable = [box for box in boxes if len(box) > 1]
        if not splittable:
            break

        box = max(splittable, key=lambda item: _box_priority(item, lab_colors))
        boxes.remove(box)
        first, second = _split_box(box, lab_colors)
        boxes.extend((first, second))

    lab_palette = [_average_lab_color(box, lab_colors) for box in boxes]
    palette = [_lab_to_rgb(color) for color in lab_palette]
    return palette, lab_palette


def _box_priority(box, lab_colors) -> tuple[float, int]:
    ranges = [
        max(lab_colors[color][channel] for color in box) - min(lab_colors[color][channel] for color in box)
        for channel in range(3)
    ]
    return max(ranges), sum(box.values())


def _split_box(box, lab_colors):
    ranges = [
        max(lab_colors[color][channel] for color in box) - min(lab_colors[color][channel] for color in box)
        for channel in range(3)
    ]
    channel = max(range(3), key=lambda index: ranges[index])
    colors = sorted(box, key=lambda color: lab_colors[color][channel])
    midpoint = sum(box.values()) / 2
    accumulated = 0
    split_index = 1

    for index, color in enumerate(colors[:-1], 1):
        accumulated += box[color]
        if accumulated >= midpoint:
            split_index = index
            break
    return ({color: box[color] for color in colors[:split_index]}, {color: box[color] for color in colors[split_index:]})


def _average_lab_color(box, lab_colors) -> tuple[float, float, float]:
    total = sum(box.values())
    return tuple(
        sum(lab_colors[color][channel] * count for color, count in box.items()) / total
        for channel in range(3)
    )


def _nearest_lab_color(pixel, palette) -> int:
    return min(
        range(len(palette)),
        key=lambda index: sum((pixel[channel] - palette[index][channel]) ** 2 for channel in range(3))
    )


def _rgb_to_lab(color: tuple[int, int, int]) -> tuple[float, float, float]:
    red, green, blue = (_srgb_to_linear(channel / 255.0) for channel in color)
    x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = (0.0193339 * red + 0.1191920 * green + 0.9503041 * blue) / 1.08883
    x, y, z = (_lab_curve(value) for value in (x, y, z))
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


def _lab_to_rgb(color: tuple[float, float, float]) -> tuple[int, int, int]:
    lightness, green_red, blue_yellow = color
    y = (lightness + 16) / 116
    x = y + green_red / 500
    z = y - blue_yellow / 200
    x = 0.95047 * _inverse_lab_curve(x)
    y = _inverse_lab_curve(y)
    z = 1.08883 * _inverse_lab_curve(z)

    red = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    green = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    blue = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    return tuple(round(255 * max(0.0, min(1.0, _linear_to_srgb(channel)))) for channel in (red, green, blue))


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(value: float) -> float:
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def _lab_curve(value: float) -> float:
    epsilon = 216 / 24389
    kappa = 24389 / 27
    return value ** (1 / 3) if value > epsilon else (kappa * value + 16) / 116


def _inverse_lab_curve(value: float) -> float:
    epsilon = 216 / 24389
    kappa = 24389 / 27
    cubed = value ** 3
    return cubed if cubed > epsilon else (116 * value - 16) / kappa

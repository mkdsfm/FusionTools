import pathlib
import struct
import sys
import tempfile
import unittest
import zlib


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from image_processing import _lab_to_rgb, _rgb_to_lab, load_and_quantize, replace_small_regions
from png_reader import ADAM7_PASSES, read_png


def chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack('>I', len(data)) + chunk_type + data + struct.pack('>I', checksum)


def filter_row(row: bytes, previous: bytes, bytes_per_pixel: int, filter_type: int) -> bytes:
    encoded = bytearray(len(row))
    for index, value in enumerate(row):
        left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        up = previous[index]
        upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = up
        elif filter_type == 3:
            predictor = (left + up) // 2
        else:
            estimate = left + up - upper_left
            distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
            predictor = (left, up, upper_left)[distances.index(min(distances))]
        encoded[index] = (value - predictor) & 0xFF
    return bytes(encoded)


def make_rgba_png(width: int, height: int, pixels, interlaced=False, filter_types=None) -> bytes:
    raw = bytearray()
    passes = ADAM7_PASSES if interlaced else ((0, 0, 1, 1),)
    row_number = 0

    for start_x, start_y, step_x, step_y in passes:
        pass_width = 0 if width <= start_x else (width - start_x + step_x - 1) // step_x
        pass_height = 0 if height <= start_y else (height - start_y + step_y - 1) // step_y
        if pass_width == 0 or pass_height == 0:
            continue

        previous = bytes(pass_width * 4)
        for pass_y in range(pass_height):
            y = start_y + pass_y * step_y
            row = bytes(channel for x in range(start_x, width, step_x) for channel in pixels[y * width + x])
            filter_type = filter_types[row_number % len(filter_types)] if filter_types else 0
            raw.append(filter_type)
            raw.extend(filter_row(row, previous, 4, filter_type))
            previous = row
            row_number += 1

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 1 if interlaced else 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b'')


def make_indexed_png() -> bytes:
    ihdr = struct.pack('>IIBBBBB', 4, 1, 2, 3, 0, 0, 0)
    palette = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0))
    transparency = bytes((255, 255, 128, 0))
    raw = bytes((0, 0b00011011))
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'PLTE', palette)
        + chunk(b'tRNS', transparency)
        + chunk(b'IDAT', zlib.compress(raw))
        + chunk(b'IEND', b'')
    )


class PngReaderTests(unittest.TestCase):
    def write_png(self, directory, data):
        path = pathlib.Path(directory) / 'source.png'
        path.write_bytes(data)
        return path

    def test_decodes_all_row_filters_and_transparency(self):
        pixels = [
            (255, 0, 0, 255), (0, 255, 0, 255),
            (0, 0, 255, 255), (0, 0, 0, 0),
            (10, 20, 30, 255), (40, 50, 60, 128),
            (70, 80, 90, 255), (100, 110, 120, 255),
            (130, 140, 150, 255), (160, 170, 180, 255)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_png(directory, make_rgba_png(2, 5, pixels, filter_types=(0, 1, 2, 3, 4)))

            image = read_png(str(path))

            self.assertEqual((2, 5), (image.width, image.height))
            self.assertEqual((255, 255, 255), image.pixels[3])
            self.assertEqual((147, 152, 157), image.pixels[5])

    def test_decodes_indexed_packed_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_png(directory, make_indexed_png())

            image = read_png(str(path))

            self.assertEqual((255, 0, 0), image.pixels[0])
            self.assertEqual((0, 255, 0), image.pixels[1])
            self.assertEqual((127, 127, 255), image.pixels[2])
            self.assertEqual((255, 255, 255), image.pixels[3])

    def test_decodes_adam7_interlacing(self):
        pixels = [
            ((x * 40) % 256, (y * 50) % 256, ((x + y) * 30) % 256, 255)
            for y in range(5)
            for x in range(6)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_png(directory, make_rgba_png(6, 5, pixels, interlaced=True))

            image = read_png(str(path))

            self.assertEqual(tuple(pixel[:3] for pixel in pixels), image.pixels)


class ImageProcessingTests(unittest.TestCase):
    def test_quantizes_to_requested_grid_and_color_limit(self):
        pixels = [
            (255, 0, 0, 255), (255, 0, 0, 255), (0, 255, 0, 255), (0, 255, 0, 255),
            (0, 0, 255, 255), (0, 0, 255, 255), (255, 255, 0, 255), (255, 255, 0, 255)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'source.png'
            path.write_bytes(make_rgba_png(4, 2, pixels))

            result = load_and_quantize(str(path), 2, 2, 2)

            self.assertEqual(2, len(result.pixels))
            self.assertTrue(all(len(row) == 2 for row in result.pixels))
            self.assertLessEqual(len(result.palette), 2)

    def test_known_cielab_coordinates(self):
        lightness, green_red, blue_yellow = _rgb_to_lab((255, 0, 0))

        self.assertAlmostEqual(53.24, lightness, places=2)
        self.assertAlmostEqual(80.09, green_red, places=2)
        self.assertAlmostEqual(67.20, blue_yellow, places=2)

    def test_cielab_round_trip_preserves_rgb(self):
        for color in ((0, 0, 0), (255, 255, 255), (12, 98, 203), (250, 80, 17)):
            converted = _lab_to_rgb(_rgb_to_lab(color))

            self.assertTrue(all(abs(actual - expected) <= 1 for actual, expected in zip(converted, color)))


class SmallRegionTests(unittest.TestCase):
    PALETTE = [
        (0, 0, 0),
        (255, 255, 255),
        (128, 128, 128),
        (120, 120, 120)
    ]

    def test_single_pixel_uses_color_with_most_touching_sides(self):
        pixels = [
            [0, 0, 0, 0, 0],
            [0, 0, 2, 1, 1],
            [0, 0, 0, 1, 1]
        ]

        result = replace_small_regions(pixels, self.PALETTE)

        self.assertEqual(0, result[1][2])

    def test_equal_contact_uses_larger_connected_region(self):
        pixels = [
            [0, 0, 0, 1, 1],
            [0, 0, 2, 1, 1],
            [0, 1, 1, 1, 1]
        ]

        result = replace_small_regions(pixels, self.PALETTE)

        self.assertEqual(1, result[1][2])

    def test_equal_contact_and_area_uses_closest_rgb_color(self):
        pixels = [
            [0, 0, 2, 3, 3],
            [0, 0, 2, 3, 3]
        ]

        result = replace_small_regions(pixels, self.PALETTE)

        self.assertEqual(3, result[0][2])

    def test_two_pixel_island_replaces_each_pixel_independently(self):
        pixels = [
            [0, 0, 0, 1, 1, 1],
            [0, 0, 2, 2, 1, 1],
            [0, 0, 0, 1, 1, 1]
        ]

        result = replace_small_regions(pixels, self.PALETTE)

        self.assertEqual(0, result[1][2])
        self.assertEqual(1, result[1][3])

    def test_pixel_is_preserved_without_adjacent_large_region(self):
        pixels = [
            [0, 1],
            [2, 3]
        ]

        result = replace_small_regions(pixels, self.PALETTE)

        self.assertEqual(pixels, result)


if __name__ == '__main__':
    unittest.main()

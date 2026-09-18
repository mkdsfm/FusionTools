import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from geometry import build_color_geometry, calculate_grid


class CalculateGridTests(unittest.TestCase):
    def test_returns_integer_grid(self):
        self.assertEqual((50, 30), calculate_grid(100, 60, 2))

    def test_rejects_non_divisible_width(self):
        with self.assertRaisesRegex(ValueError, 'Width'):
            calculate_grid(101, 60, 2)

    def test_rejects_non_divisible_height(self):
        with self.assertRaisesRegex(ValueError, 'Height'):
            calculate_grid(100, 61, 2)


class BoundaryTests(unittest.TestCase):
    def test_solid_rectangle_becomes_four_lines(self):
        geometry = build_color_geometry([[0, 0, 0], [0, 0, 0]], [(255, 0, 0)])

        self.assertEqual(1, len(geometry))
        self.assertEqual(4, len(geometry[0].segments))

    def test_internal_shared_edge_is_removed(self):
        geometry = build_color_geometry([[0, 0]], [(255, 0, 0)])
        segments = geometry[0].segments

        self.assertEqual(4, len(segments))
        self.assertFalse(any(segment.x1 == segment.x2 == 1 for segment in segments))

    def test_hole_is_preserved(self):
        pixels = [
            [0, 0, 0],
            [0, 1, 0],
            [0, 0, 0]
        ]

        geometry = build_color_geometry(pixels, [(255, 0, 0), (255, 255, 255)])

        self.assertEqual(8, len(geometry[0].segments))
        self.assertEqual(4, len(geometry[1].segments))

    def test_disconnected_islands_remain_separate_boundaries(self):
        geometry = build_color_geometry([[0, 1, 0]], [(255, 0, 0), (255, 255, 255)])

        self.assertEqual(8, len(geometry[0].segments))


if __name__ == '__main__':
    unittest.main()

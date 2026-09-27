import unittest

from bonus import pair_bonus
from lane import lane_points


class PairTest(unittest.TestCase):
    def test_lane_miss(self):
        self.assertEqual(lane_points(0), 0)

    def test_lane_one_hit(self):
        self.assertEqual(lane_points(1), 10)

    def test_lane_two_hits(self):
        self.assertEqual(lane_points(2), 15)

    def test_lane_three_hits(self):
        self.assertEqual(lane_points(3), 20)

    def test_bonus_needs_both_sides(self):
        self.assertEqual(pair_bonus(2, 0), 0)
        self.assertEqual(pair_bonus(0, 3), 0)

    def test_bonus_is_flat(self):
        self.assertEqual(pair_bonus(1, 1), 7)
        self.assertEqual(pair_bonus(3, 4), 7)

    def test_total_uses_both_modules(self):
        total = lane_points(2) + lane_points(1) + pair_bonus(2, 1)
        self.assertEqual(total, 32)


if __name__ == "__main__":
    unittest.main()

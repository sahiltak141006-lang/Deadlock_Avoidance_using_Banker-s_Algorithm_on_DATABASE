import unittest
from bankers import safety_check
from scheduler import AgingScheduler


class TestBankerAndAging(unittest.TestCase):
    def test_higher_priority_selected_among_safe_candidates(self):
        result = safety_check([3], [[0], [0]], [[2], [1]],
                              ["LOW", "HIGH"], [1, 5])
        self.assertTrue(result.safe)
        self.assertEqual(result.safe_sequence[0], "HIGH")

    def test_unsafe_claim(self):
        result = safety_check([2], [[0]], [[3]], ["A"], [1])
        self.assertFalse(result.safe)

    def test_priority_starts_at_one(self):
        self.assertEqual(AgingScheduler().priority(1, 0), 1)

    def test_priority_ages_after_each_completion(self):
        aging = AgingScheduler()
        self.assertEqual(aging.priority(1, 1), 2)
        self.assertEqual(aging.priority(1, 2), 3)

    def test_priority_cap(self):
        aging = AgingScheduler()
        self.assertEqual(aging.priority(1, 10), 5)
        self.assertEqual(aging.priority(4, 10), 5)

    def test_fifo_tie_break(self):
        result = safety_check([5], [[0], [0], [0]], [[1], [1], [1]],
                              ["FIRST", "SECOND", "THIRD"], [1, 1, 1])
        self.assertEqual(result.safe_sequence, ["FIRST", "SECOND", "THIRD"])

    def test_safety_precedes_priority(self):
        result = safety_check([2], [[0], [0]], [[3], [1]],
                              ["HIGH", "SAFE"], [5, 1])
        self.assertFalse(result.safe)


if __name__ == "__main__":
    unittest.main()

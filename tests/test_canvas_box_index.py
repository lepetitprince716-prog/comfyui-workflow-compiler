"""cwc.canvas.box_index answers as cwc.canvas.links.crosses does, box by box."""

from __future__ import annotations

import random
import time
import unittest
from unittest import mock

from cwc.canvas import box_index
from cwc.canvas.box_index import BUCKET, MAX_BUCKETS, BoxIndex
from cwc.canvas.geometry import Rect
from cwc.canvas.links import crosses


def _brute(path, boxes, exclude):
    return tuple(key for key, box in boxes if key not in exclude and crosses(path, box))


class BoxIndexTest(unittest.TestCase):
    def test_random_paths_match_box_by_box(self):
        rng = random.Random(7)
        for _ in range(40):
            boxes = []
            for i in range(rng.randint(1, 30)):
                # snapped corners make paths along and onto box edges common
                x, y = rng.randint(-20, 20) * 40.0, rng.randint(-20, 20) * 40.0
                boxes.append((str(i), Rect(x, y, rng.randint(1, 12) * 40.0, rng.randint(1, 8) * 40.0)))
            index = BoxIndex(boxes)
            for _ in range(30):
                n = rng.randint(2, 70)  # past one window of CHUNK segments
                if rng.random() < 0.5:
                    path = [(rng.randint(-25, 25) * 40.0, rng.randint(-25, 25) * 40.0) for _ in range(n)]
                else:
                    path = [(rng.uniform(-1000, 1000), rng.uniform(-1000, 1000)) for _ in range(n)]
                exclude = {str(rng.randrange(len(boxes)))}
                self.assertEqual(index.crossed(path, exclude), _brute(path, boxes, exclude))

    def test_a_segment_of_no_length_matches_box_by_box(self):
        rng = random.Random(11)
        for _ in range(40):
            boxes = []
            for i in range(rng.randint(1, 30)):
                x, y = rng.randint(-20, 20) * 40.0, rng.randint(-20, 20) * 40.0
                boxes.append((str(i), Rect(x, y, rng.randint(1, 12) * 40.0, rng.randint(1, 8) * 40.0)))
            index = BoxIndex(boxes)
            for _ in range(60):
                # on edges, corners and bucket lines as often as inside
                if rng.random() < 0.5:
                    point = (rng.randint(-25, 25) * 40.0, rng.randint(-25, 25) * 40.0)
                else:
                    point = (rng.uniform(-1000, 1000), rng.uniform(-1000, 1000))
                exclude = {str(rng.randrange(len(boxes)))}
                self.assertEqual(index.crossed([point, point], exclude), _brute([point, point], boxes, exclude))
                self.assertEqual(index.crossed([point, point]), _brute([point, point], boxes, ()))

    def test_edges_do_not_count(self):
        index = BoxIndex([("a", Rect(0, 0, 100, 50))])
        self.assertEqual(index.crossed([(0, -10), (0, 60)]), ())
        self.assertEqual(index.crossed([(-10, 50), (110, 50)]), ())
        self.assertEqual(index.crossed([(-10, 25), (110, 25)]), ("a",))

    def test_long_segment_over_many_buckets(self):
        boxes = [(str(i), Rect(i * 3 * BUCKET, 0, 50, 50)) for i in range(4)]
        index = BoxIndex(boxes)
        path = [(-10.0, 25.0), (20 * BUCKET, 25.0)]
        self.assertEqual(index.crossed(path), ("0", "1", "2", "3"))
        self.assertEqual(index.crossed(path, {"2"}), ("0", "1", "3"))

    def test_answer_in_index_order(self):
        boxes = [("9", Rect(200, 0, 50, 50)), ("10", Rect(0, 0, 50, 50))]
        self.assertEqual(BoxIndex(boxes).crossed([(-10, 25), (300, 25)]), ("9", "10"))


def _mixed_boxes(rng, huge):
    """Ordinary boxes snapped to a grid, and ``huge`` boxes over more than MAX_BUCKETS buckets,
    some of them reaching into the region the paths run in, at random places in the order."""
    boxes = []
    for _ in range(rng.randint(1, 30)):
        x, y = rng.randint(-20, 20) * 40.0, rng.randint(-20, 20) * 40.0
        boxes.append((Rect(x, y, rng.randint(1, 12) * 40.0, rng.randint(1, 8) * 40.0)))
    side = (MAX_BUCKETS**0.5 + 2) * BUCKET
    for _ in range(huge):
        w, h = side * rng.uniform(1.0, 4.0), side * rng.uniform(1.0, 4.0)
        boxes.insert(rng.randint(0, len(boxes)), Rect(rng.uniform(-w, 600.0), rng.uniform(-h, 600.0), w, h))
    return [(str(i), box) for i, box in enumerate(boxes)]


class LargeBoxTest(unittest.TestCase):
    def check_matches(self, seed, huge):
        rng = random.Random(seed)
        crossings = 0
        for _ in range(60):
            boxes = _mixed_boxes(rng, huge)
            index = BoxIndex(boxes)
            for _ in range(10):
                n = rng.randint(2, 70)
                if rng.random() < 0.5:
                    path = [(rng.randint(-25, 25) * 40.0, rng.randint(-25, 25) * 40.0) for _ in range(n)]
                else:
                    path = [(rng.uniform(-1000, 1000), rng.uniform(-1000, 1000)) for _ in range(n)]
                if rng.random() < 0.2:
                    path = [path[0], path[0]]  # a segment of no length
                exclude = {str(rng.randrange(len(boxes)))} if rng.random() < 0.7 else set()
                got = index.crossed(path, exclude)
                self.assertEqual(got, _brute(path, boxes, exclude))
                crossings += len(got)
        self.assertGreater(crossings, 500)  # the comparison is not between empty answers

    def test_a_box_over_the_limit_is_checked_every_time(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                self.check_matches(seed, huge=seed)  # one to three large boxes among the ordinary ones

    def test_large_boxes_are_in_no_bucket_and_ordinary_ones_are(self):
        boxes = [("a", Rect(0, 0, 50, 50)), ("b", Rect(-1e5, -1e5, 2e5, 2e5)), ("c", Rect(300, 0, 50, 50))]
        index = BoxIndex(boxes)
        self.assertEqual(index.large, [1])
        self.assertEqual(sorted({i for held in index.buckets.values() for i in held}), [0, 2])
        self.assertEqual(index.crossed([(10, 25), (320, 25)]), ("a", "b", "c"))
        self.assertEqual(index.crossed([(10, 25), (10, 25)]), ("a", "b"))
        self.assertEqual(index.crossed([(10, 25), (320, 25)], {"b"}), ("a", "c"))

    def test_every_box_large_when_the_limit_is_tiny(self):
        # the limit moved down so that ordinary boxes take the large path too
        with mock.patch.object(box_index, "MAX_BUCKETS", 1):
            rng = random.Random(5)
            for _ in range(40):
                boxes = _mixed_boxes(rng, 0)
                index = BoxIndex(boxes)
                self.assertTrue(index.large)
                for _ in range(10):
                    path = [(rng.uniform(-1000, 1000), rng.uniform(-1000, 1000)) for _ in range(rng.randint(2, 50))]
                    self.assertEqual(index.crossed(path), _brute(path, boxes, ()))

    def test_a_huge_box_builds_at_once(self):
        best = min(self.build_time() for _ in range(3))
        self.assertLess(best, 0.05)

    def build_time(self):
        start = time.perf_counter()
        BoxIndex([("a", Rect(0.0, 0.0, 3e5, 3e5)), ("b", Rect(10.0, 10.0, 50.0, 50.0))])
        return time.perf_counter() - start


if __name__ == "__main__":
    unittest.main()

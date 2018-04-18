import unittest

from diffengine import FeedProcessor


class TestDiffengine(unittest.TestCase):
    def test_smoke_FeedProcessor(self):
        proc = FeedProcessor({})
        proc.process_feed_entries([], None)
        self.assertEquals(proc.stats(), "new: 0, checked: 0, skipped: 0, diffs: 0, tweeted: 0")

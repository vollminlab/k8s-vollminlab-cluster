"""Tests for warm.py. Pure stdlib, no cluster or Jellyfin needed:

    python3 warm_test.py
"""

import unittest

import warm


def sub(index, codec="subrip", external=False, text=True, supports_external=True):
    return {
        "Type": "Subtitle",
        "Index": index,
        "Codec": codec,
        "IsExternal": external,
        "IsTextSubtitleStream": text,
        "SupportsExternalStream": supports_external,
    }


def item(item_id, *sources, name="Movie", **extra):
    return {"Id": item_id, "Name": name, "Type": "Movie", "MediaSources": list(sources), **extra}


def source(msid, *streams):
    return {"Id": msid, "MediaStreams": [{"Type": "Video", "Index": 0}, *streams]}


class PickStreamTest(unittest.TestCase):
    def test_first_embedded_text_subtitle(self):
        ms = source("a", sub(2, external=True), sub(3), sub(4))
        self.assertEqual(warm.pick_stream(ms), 3)

    def test_no_subtitles(self):
        self.assertIsNone(warm.pick_stream(source("a")))

    def test_external_srt_is_ignored(self):
        # A sidecar .srt is read directly; it never triggers a full-file read.
        self.assertIsNone(warm.pick_stream(source("a", sub(2, external=True))))

    def test_image_only_subtitles_are_ignored(self):
        # Clients only fetch text subtitles out-of-band (StreamBuilder), so a
        # PGS-only file never stalls playback.
        self.assertIsNone(warm.pick_stream(source("a", sub(2, codec="hdmv_pgs_subtitle", text=False))))

    def test_source_with_any_image_subtitle_is_skipped(self):
        # Fetching the text track would extract every track, PGS included, and
        # PGS caches at ~18 MB per track (median, 2026-10-14).
        ms = source("a", sub(2), sub(3, codec="hdmv_pgs_subtitle", text=False))
        self.assertIsNone(warm.pick_stream(ms))

    def test_external_image_subtitle_does_not_skip(self):
        # A sidecar is never extracted, so it adds nothing to the cache.
        ms = source("a", sub(2), sub(3, codec="hdmv_pgs_subtitle", text=False, external=True))
        self.assertEqual(warm.pick_stream(ms), 2)

    def test_stream_without_external_support_is_ignored(self):
        self.assertIsNone(warm.pick_stream(source("a", sub(2, supports_external=False))))


class TargetsTest(unittest.TestCase):
    def test_one_target_per_media_source(self):
        items = [
            item("i1", source("m1", sub(2)), source("m2", sub(5))),
            item("i2", source("m3")),
        ]
        got = [(t.item_id, t.source_id, t.index) for t in warm.targets(items)]
        self.assertEqual(got, [("i1", "m1", 2), ("i1", "m2", 5)])

    def test_episode_label(self):
        ep = item("e1", source("m1", sub(2)), name="Pilot", Type="Episode",
                  SeriesName="Show", ParentIndexNumber=1, IndexNumber=3)
        self.assertEqual(warm.label(ep), "Show S01E03 Pilot")

    def test_movie_label(self):
        self.assertEqual(warm.label(item("i", name="GoldenEye", ProductionYear=1995)), "GoldenEye (1995)")


class ListItemsTest(unittest.TestCase):
    def test_pages_until_exhausted(self):
        pages = {0: [{"Id": "a"}, {"Id": "b"}], 2: [{"Id": "c"}]}
        seen = []

        def fetch_json(path):
            seen.append(path)
            start = int(path.split("StartIndex=")[1].split("&")[0])
            return {"Items": pages.get(start, []), "TotalRecordCount": 3}

        got = warm.list_items(fetch_json, page_size=2)
        self.assertEqual([i["Id"] for i in got], ["a", "b", "c"])
        self.assertEqual(len(seen), 2)
        self.assertIn("IncludeItemTypes=Movie,Episode", seen[0])
        self.assertIn("SortBy=DateCreated", seen[0])
        self.assertIn("SortOrder=Descending", seen[0])


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RunTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.logs = []
        self.tgts = [warm.Target("i%d" % n, "m%d" % n, 2, "Movie %d" % n) for n in range(3)]

    def run_warm(self, fetch, budget=1000, slow=5):
        return warm.run(self.tgts, fetch, clock=self.clock, budget_seconds=budget,
                        slow_seconds=slow, log=self.logs.append)

    def test_requests_subtitle_url_for_each_target(self):
        paths = []
        stats = self.run_warm(lambda p: paths.append(p))
        self.assertEqual(paths[0], "/Videos/i0/m0/Subtitles/2/0/Stream.srt")
        self.assertEqual(len(paths), 3)
        self.assertEqual(stats.cached, 3)
        self.assertEqual(stats.extracted, 0)

    def test_slow_request_counts_as_extracted_and_is_logged(self):
        def fetch(path):
            if "i1" in path:
                self.clock.now += 74
        stats = self.run_warm(fetch)
        self.assertEqual((stats.extracted, stats.cached), (1, 2))
        self.assertTrue(any("Movie 1" in l and "74" in l for l in self.logs))

    def test_failure_is_logged_with_reason_and_run_continues(self):
        def fetch(path):
            if "i0" in path:
                raise warm.FetchError("HTTP 500: boom")
        stats = self.run_warm(fetch)
        self.assertEqual(stats.failed, 1)
        self.assertEqual(stats.cached, 2)
        self.assertTrue(any("Movie 0" in l and "HTTP 500: boom" in l for l in self.logs))

    def test_stops_starting_new_requests_once_budget_spent(self):
        def fetch(path):
            self.clock.now += 600
        stats = self.run_warm(fetch, budget=1000)
        # t=0 start i0 -> 600, start i1 -> 1200, budget gone, i2 not started
        self.assertEqual(stats.extracted, 2)
        self.assertEqual(stats.remaining, 1)
        self.assertTrue(any("budget" in l for l in self.logs))


class ExitCodeTest(unittest.TestCase):
    def test_all_failed_is_an_error(self):
        self.assertEqual(warm.exit_code(warm.Stats(failed=3)), 1)

    def test_some_failed_is_not(self):
        # One unreadable file must not page every night; it is logged instead.
        self.assertEqual(warm.exit_code(warm.Stats(failed=1, cached=10)), 0)

    def test_nothing_to_do_is_fine(self):
        self.assertEqual(warm.exit_code(warm.Stats()), 0)


if __name__ == "__main__":
    unittest.main()

"""Pre-extract embedded subtitles so Jellyfin playback starts immediately.

On first play, clients fetch each embedded text subtitle out-of-band. Jellyfin
serves that by extracting every subtitle track in one ffmpeg pass, and because
subtitle packets are interleaved through the whole mkv, that pass reads the
entire file over CIFS before playback can begin: 74s for a 20 GB 4K movie,
2m25s for a 24 GB remux with 46 tracks (measured 2026-10-11, Jellyfin 10.11.8).

The result is cached in /config/data/subtitles, and ExtractAllExtractableSubtitles
skips any track whose cache file already exists. So requesting one text track
per media source warms all of them, and repeating the request is cheap. That
makes a nightly walk of the whole library safe: the first runs backfill, and
later runs only pay for newly imported items.

Newest items go first, so fresh imports are warmed before the backfill tail.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class FetchError(Exception):
    pass


@dataclass
class Target:
    item_id: str
    source_id: str
    index: int
    label: str


@dataclass
class Stats:
    cached: int = 0
    extracted: int = 0
    failed: int = 0
    remaining: int = 0


def pick_stream(media_source):
    """Index of the first subtitle a client would fetch out-of-band, or None."""
    for s in media_source.get("MediaStreams") or []:
        if (
            s.get("Type") == "Subtitle"
            and not s.get("IsExternal")
            and s.get("IsTextSubtitleStream")
            and s.get("SupportsExternalStream")
        ):
            return s["Index"]
    return None


def label(item):
    if item.get("Type") == "Episode":
        return "%s S%02dE%02d %s" % (
            item.get("SeriesName", "?"),
            item.get("ParentIndexNumber") or 0,
            item.get("IndexNumber") or 0,
            item.get("Name", "?"),
        )
    year = item.get("ProductionYear")
    return "%s (%s)" % (item.get("Name", "?"), year) if year else item.get("Name", "?")


def targets(items):
    for item in items:
        for ms in item.get("MediaSources") or []:
            index = pick_stream(ms)
            if index is not None:
                yield Target(item["Id"], ms["Id"], index, label(item))


def list_items(fetch_json, page_size=200):
    # Collected up front rather than processed page by page: a run lasts hours,
    # and an import landing mid-run would shift a DateCreated-sorted page window.
    items, start = [], 0
    while True:
        page = fetch_json(
            "/Items?Recursive=true&IncludeItemTypes=Movie,Episode"
            "&Fields=MediaSources&SortBy=DateCreated&SortOrder=Descending"
            "&StartIndex=%d&Limit=%d" % (start, page_size)
        )
        batch = page.get("Items") or []
        items.extend(batch)
        start += len(batch)
        if not batch or start >= page.get("TotalRecordCount", 0):
            return items


def run(tgts, fetch, clock, budget_seconds, slow_seconds, log):
    stats = Stats()
    deadline = clock() + budget_seconds
    for n, t in enumerate(tgts):
        if clock() >= deadline:
            stats.remaining = len(tgts) - n
            log("budget of %ds spent; %d left for the next run" % (budget_seconds, stats.remaining))
            break
        started = clock()
        try:
            fetch("/Videos/%s/%s/Subtitles/%d/0/Stream.srt" % (t.item_id, t.source_id, t.index))
        except FetchError as e:
            stats.failed += 1
            log("FAILED %s (item %s): %s" % (t.label, t.item_id, e))
            continue
        took = clock() - started
        if took >= slow_seconds:
            stats.extracted += 1
            log("extracted %s in %ds" % (t.label, took))
        else:
            stats.cached += 1
    return stats


def exit_code(stats):
    # Fail only when nothing worked (auth, Jellyfin down). A single unreadable
    # file is logged above with its reason rather than failing every night.
    attempted = stats.cached + stats.extracted + stats.failed
    return 1 if attempted and stats.failed == attempted else 0


def main():
    base = os.environ["JELLYFIN_URL"].rstrip("/")
    headers = {"Authorization": 'MediaBrowser Token="%s"' % os.environ["JELLYFIN_API_KEY"]}
    timeout = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "3600"))

    def get(path, t):
        req = urllib.request.Request(base + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=t) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise FetchError("HTTP %d: %s" % (e.code, e.read(200).decode("utf-8", "replace")))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(str(e))

    log = lambda msg: print(msg, flush=True)
    items = list_items(lambda p: json.loads(get(p, 120)))
    tgts = list(targets(items))
    log("%d items, %d media sources with embedded text subtitles" % (len(items), len(tgts)))

    stats = run(
        tgts,
        lambda p: get(p, timeout),
        clock=time.monotonic,
        budget_seconds=int(os.environ.get("MAX_RUNTIME_SECONDS", "18000")),
        slow_seconds=int(os.environ.get("SLOW_SECONDS", "5")),
        log=log,
    )
    log("done: extracted=%d cached=%d failed=%d remaining=%d"
        % (stats.extracted, stats.cached, stats.failed, stats.remaining))
    return exit_code(stats)


if __name__ == "__main__":
    sys.exit(main())

import os
import time
import zipfile
from pathlib import Path

import pytest
from mediafile import MediaFile

import intake

# A few silent MPEG-1 Layer III frames: enough for mutagen to read and write tags.
MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 413
MP3 = MP3 * 8

KNOWN = {  # (artist, album) the fake Audible's SEARCH finds -> (author, title, language)
    ("Andy Weir", "Artemis"): ("Andy Weir", "Artemis", "English"),
    ("George Orwell", "Animal Farm"): ("George Orwell", "Animal Farm", "English"),
    ("Brandon Sanderson", "Mistborn"): ("Brandon Sanderson", "The Final Empire", "English"),
    # The 2026-10-04 mislabel: a tag that is a substring of the German edition's title.
    ("Michael Cheney", "Confessions of a Trash Droid: The Complete Series in One"):
        ("Michael Cheney", "Confessions of a Trash Droid_ The Complete Series in One (German Edition)", "German"),
    ("Michael Cheney", "Confessions of a Trash Droid"):
        ("Michael Cheney", "Confessions of a Trash Droid", "English"),
}
ASINS = {  # what --search-id resolves to: asin -> (author, title, language, files it fits)
    "B0GWFGGR9J": ("Michael Cheney", "Confessions of a Trash Droid: The Complete First Series in One", "English", 1),
    "B0HJZV1VGW": ("Michael Cheney", "Confessions of a Trash Droid: The Complete Series in One (German Edition)",
                   "German", 1),
    "1980004900": ("Tamsyn Muir", "Gideon the Ninth", "English", 1),
}


def fake_lookup(asin):
    if asin not in ASINS:
        return None
    author, title, lang, _ = ASINS[asin]
    return author, title, lang


def mp3(path: Path, album=None, artist=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MP3)
    if album or artist:
        m = MediaFile(path)
        m.album, m.artist = album, artist
        m.save()
    return path


def age(root: Path, seconds=3600) -> None:
    t = time.time() - seconds
    for p in [root, *root.rglob("*")]:
        os.utime(p, (t, t))


class FakeBeets:
    """Moves a book into staging iff it would be a confident beets-audible
    match, like the real import; otherwise leaves it alone, like a skip.
    Search uses the files' tags against KNOWN; --search-id uses ASINS."""

    def __init__(self, staging: Path):
        self.staging = staging
        self.calls: list[tuple[str, str]] = []
        self.search_ids: list[str | None] = []
        self.seen_asin: list[str | None] = []  # the ASIN tag each import was handed

    def import_book(self, book: Path, *, search_id=None, threshold=None) -> None:
        files = intake.audio_files(book)
        m = MediaFile(files[0])
        key = (m.artist or "", m.album or "")
        self.calls.append(key)
        self.search_ids.append(search_id)
        self.seen_asin.append(m.asin or None)
        if search_id:
            if search_id not in ASINS or ASINS[search_id][3] != len(files):
                return
            author, title, lang, _ = ASINS[search_id]
        elif key in KNOWN:
            author, title, lang = KNOWN[key]
        else:
            return
        dest = self.staging / author / title.replace(":", "_")
        dest.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(intake.os_sorted(files), 1):
            target = dest / f"{i:02d} - {title.replace(':', '_')}{f.suffix}"
            f.rename(target)
            t = MediaFile(target)
            t.album, t.artist, t.albumartist, t.language = title, author, author, lang
            if lang == "German":
                t.asin = "B0HJZV1VGW"  # the real plugin stamps the matched edition's ASIN
            t.save()
        (dest / "cover.jpg").write_bytes(b"jpg")


@pytest.fixture
def env(tmp_path):
    incoming, library = tmp_path / "incoming", tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    (incoming / intake.STAGING_NAME).mkdir()
    beets = FakeBeets(incoming / intake.STAGING_NAME)
    it = intake.Intake(incoming, library, beets, quiet_seconds=900, lookup=fake_lookup)
    return it, incoming, library, beets


def review_names(incoming: Path) -> set[str]:
    r = incoming / intake.REVIEW_NAME
    return {p.name for p in r.iterdir() if not p.name.endswith(".txt")} if r.exists() else set()


# ------------------------------------------------------------ pure helpers


@pytest.mark.parametrize("raw,clean", [
    ("The Martian (Unabridged)", "The Martian"),
    ("Animal Farm [B002V5H6F4]", "Animal Farm"),
    ("Dune (2007)", "Dune"),
    ("andy_weir_-_artemis", "andy weir - artemis"),
])
def test_clean_name(raw, clean):
    assert intake.clean_name(raw) == clean


def test_seed_attempts_prefers_file_tags_then_both_name_orders():
    tagged = {Path("a.mp3"): ("Artemis", "Andy Weir", "")}
    assert intake.seed_attempts("Andy Weir - Artemis", tagged) == [
        None, ("Andy Weir", "Artemis"), ("Artemis", "Andy Weir")]


def test_seed_attempts_untagged_title_only_folder():
    assert intake.seed_attempts("Project Hail Mary", {Path("a"): ("", "", "")}) == [("", "Project Hail Mary")]


@pytest.mark.parametrize("name", ["CD1", "cd 2", "Disc 03", "disk1", "Part 4", "Volume 2"])
def test_disc_dir_pattern(name):
    assert intake.DISC_DIR.match(name)


@pytest.mark.parametrize("name", ["Mistborn", "Part of the Story", "CD Collection"])
def test_disc_dir_pattern_rejects_titles(name):
    assert not intake.DISC_DIR.match(name)


# ------------------------------------------------------------- end to end


def test_untagged_folder_in_author_title_order_is_filed(env):
    it, incoming, library, _ = env
    for i in (1, 2):
        mp3(incoming / "Andy Weir - Artemis" / f"{i:02d}.mp3")
    age(incoming)
    it.run()
    assert sorted(p.name for p in (library / "Andy Weir" / "Artemis").iterdir()) == [
        "01 - Artemis.mp3", "02 - Artemis.mp3", "cover.jpg"]
    assert not (incoming / "Andy Weir - Artemis").exists()
    assert it.filed == ["Andy Weir/Artemis"]


def test_loose_file_in_title_author_order_is_wrapped_and_filed(env):
    it, incoming, library, beets = env
    mp3(incoming / "Animal Farm - George Orwell.mp3")
    age(incoming)
    it.run()
    assert (library / "George Orwell" / "Animal Farm" / "01 - Animal Farm.mp3").exists()
    # the reversed order was needed, and was tried second
    assert beets.calls == [("Animal Farm", "George Orwell"), ("George Orwell", "Animal Farm")]


def test_zip_with_disc_folders_is_extracted_flattened_and_filed(env, tmp_path):
    it, incoming, library, _ = env
    src = tmp_path / "src" / "Mistborn"
    mp3(src / "CD1" / "Track 1.mp3", album="Mistborn", artist="Brandon Sanderson")
    mp3(src / "CD2" / "Track 1.mp3", album="Mistborn", artist="Brandon Sanderson")
    (src / "__MACOSX").mkdir()
    with zipfile.ZipFile(incoming / "mistborn.zip", "w") as z:
        for f in src.parent.rglob("*"):
            z.write(f, f.relative_to(src.parent))
    age(incoming)
    it.run()
    filed = library / "Brandon Sanderson" / "The Final Empire"
    assert len(intake.audio_files(filed)) == 2
    assert not any(p for p in incoming.iterdir() if not p.name.startswith((".", "_")))


def test_container_folder_with_two_books_files_both(env):
    it, incoming, library, _ = env
    mp3(incoming / "From Dave" / "Andy Weir - Artemis" / "a.mp3")
    mp3(incoming / "From Dave" / "George Orwell - Animal Farm" / "a.mp3")
    age(incoming)
    it.run()
    assert sorted(it.filed) == ["Andy Weir/Artemis", "George Orwell/Animal Farm"]
    assert not (incoming / "From Dave").exists()


def test_unidentifiable_book_goes_to_review_with_original_tags(env):
    it, incoming, library, _ = env
    f = mp3(incoming / "new audiobook" / "audio.mp3", album="Track 01", artist="Unknown")
    age(incoming)
    it.run()
    reviewed = incoming / intake.REVIEW_NAME / "new audiobook"
    assert (reviewed / "WHY-NOT-FILED.txt").read_text().startswith("no confident Audible match")
    m = MediaFile(reviewed / "audio.mp3")
    assert (m.album, m.artist) == ("Track 01", "Unknown")  # our seeded guesses were rolled back
    assert not any(library.iterdir())
    assert not f.exists()


def test_duplicate_of_library_book_goes_to_review_and_library_untouched(env):
    it, incoming, library, _ = env
    existing = library / "Andy Weir" / "Artemis"
    existing.mkdir(parents=True)
    (existing / "original.m4b").write_bytes(b"keep")
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")
    age(incoming)
    it.run()
    assert [p.name for p in existing.iterdir()] == ["original.m4b"]
    assert review_names(incoming) == {"Andy Weir - Artemis (duplicate)"}
    note = (incoming / intake.REVIEW_NAME / "Andy Weir - Artemis (duplicate)" / "WHY-NOT-FILED.txt").read_text()
    assert "already in the library" in note and "To retry" not in note
    assert it.filed == []


def test_recent_upload_is_left_alone(env):
    it, incoming, library, beets = env
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")  # mtime = now: still uploading
    it.run()
    assert beets.calls == []
    assert (incoming / "Andy Weir - Artemis" / "a.mp3").exists()


def test_review_and_hidden_folders_are_never_processed(env):
    it, incoming, library, beets = env
    mp3(incoming / intake.REVIEW_NAME / "Andy Weir - Artemis" / "a.mp3")
    mp3(incoming / ".something" / "a.mp3")
    age(incoming)
    it.run()
    assert beets.calls == []


def test_unsupported_archive_goes_to_review(env):
    it, incoming, library, beets = env
    (incoming / "Book").mkdir()
    (incoming / "Book" / "book.rar").write_bytes(b"rar")
    age(incoming)
    it.run()
    note = (incoming / intake.REVIEW_NAME / "Book" / "WHY-NOT-FILED.txt").read_text()
    assert "re-upload as .zip" in note
    assert beets.calls == []


def test_zip_with_path_traversal_is_rejected(env):
    it, incoming, library, _ = env
    with zipfile.ZipFile(incoming / "evil.zip", "w") as z:
        z.writestr("../../escape.mp3", MP3)
    age(incoming)
    it.run()
    assert not (incoming.parent / "escape.mp3").exists()
    assert "evil.zip" in review_names(incoming)


def test_ebook_alongside_audio_is_carried_into_library(env):
    it, incoming, library, _ = env
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")
    (incoming / "Andy Weir - Artemis" / "artemis.epub").write_bytes(b"epub")
    (incoming / "Andy Weir - Artemis" / "info.nfo").write_bytes(b"junk")
    age(incoming)
    it.run()
    names = {p.name for p in (library / "Andy Weir" / "Artemis").iterdir()}
    assert "artemis.epub" in names and "info.nfo" not in names


def test_recover_removes_partial_copy_and_finishes_staged_book(env):
    it, incoming, library, _ = env
    partial = library / "Andy Weir" / ("Artemis" + intake.PARTIAL_SUFFIX)
    partial.mkdir(parents=True)
    staged = incoming / intake.STAGING_NAME / "Andy Weir" / "Artemis"
    staged.mkdir(parents=True)
    (staged / "01 - Artemis.mp3").write_bytes(MP3)
    it.run()
    assert not partial.exists()
    assert (library / "Andy Weir" / "Artemis" / "01 - Artemis.mp3").exists()
    assert not staged.exists()


def test_dry_run_changes_nothing(env):
    it, incoming, library, beets = env
    it.dry_run = True
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")
    age(incoming)
    it.run()
    assert beets.calls == []
    assert (incoming / "Andy Weir - Artemis" / "a.mp3").exists()


def test_beets_config_renders():
    cfg = intake.BEETS_CONFIG.format(staging="/s", db="/d", threshold=0.15, region="us")
    import yaml
    parsed = yaml.safe_load(cfg)
    assert parsed["directory"] == "/s"
    assert parsed["match"]["strong_rec_thresh"] == 0.15
    assert parsed["import"]["quiet_fallback"] == "skip"
    assert "tracktotal" in parsed["item_fields"]["book_file"]


# ------------------------------------------------------------ completeness

TESTDATA = Path(__file__).parent / "testdata"


def box(kind: bytes, payload_len: int, declared: int | None = None) -> bytes:
    import struct
    return struct.pack(">I", declared if declared is not None else 8 + payload_len) + kind + b"\0" * payload_len


def test_mp4_complete_and_truncated(tmp_path):
    f = tmp_path / "book.m4b"
    f.write_bytes(box(b"ftyp", 16) + box(b"moov", 100) + box(b"mdat", 5000))
    assert intake.mp4_boxes_complete(f)
    # Same bytes, as seen halfway through an upload.
    assert not intake.mp4_boxes_complete(f, size=f.stat().st_size // 2)
    # moov-at-end layout, mid-upload: still inside mdat, moov not yet written.
    f.write_bytes(box(b"ftyp", 16) + box(b"mdat", 5000)[:2000])
    assert not intake.mp4_boxes_complete(f)


def test_mp4_largesize_and_to_eof_boxes(tmp_path):
    import struct
    f = tmp_path / "big.m4b"
    payload = b"\0" * 64
    f.write_bytes(box(b"ftyp", 8) + struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 16 + 64) + payload)
    assert intake.mp4_boxes_complete(f)
    f.write_bytes(box(b"ftyp", 8) + struct.pack(">I", 0) + b"mdat" + payload)  # size 0 = runs to EOF
    assert intake.mp4_boxes_complete(f)


def test_mp4_garbage_is_not_complete(tmp_path):
    f = tmp_path / "junk.m4b"
    f.write_bytes(b"\x00\x00\x00\x02" + b"x" * 100)  # box smaller than its own header
    assert not intake.mp4_boxes_complete(f)


def test_mp3_with_info_header_complete_and_truncated(tmp_path):
    src = TESTDATA / "cbr-info-header.mp3"
    assert intake.mp3_complete(src)
    half = tmp_path / "half.mp3"
    half.write_bytes(src.read_bytes()[: src.stat().st_size // 2])
    assert not intake.mp3_complete(half)


def test_zip_complete_and_truncated(tmp_path):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("book/a.mp3", MP3 * 50)
    assert intake.file_complete(z)
    z.write_bytes(z.read_bytes()[:-30])  # lose the central directory
    assert not intake.file_complete(z)


def test_incomplete_upload_waits_instead_of_review(env, caplog):
    it, incoming, library, beets = env
    d = incoming / "Andy Weir - Artemis"
    d.mkdir()
    (d / "a.m4b").write_bytes(box(b"ftyp", 16) + box(b"mdat", 5000)[:1000])
    age(incoming)  # quiet for an hour, but structurally unfinished
    caplog.set_level("INFO")
    it.run()
    it.run()
    assert beets.calls == []
    assert (d / "a.m4b").exists()
    assert not (incoming / intake.REVIEW_NAME).exists()
    waits = [r for r in caplog.records if "is incomplete" in r.getMessage()]
    assert len(waits) == 1, "a held upload must be logged once, not every pass"


def test_incomplete_upload_goes_to_review_after_giveup(env):
    it, incoming, library, beets = env
    it.incomplete_giveup_seconds = 3600
    d = incoming / "Andy Weir - Artemis"
    d.mkdir()
    (d / "a.m4b").write_bytes(box(b"ftyp", 16) + box(b"mdat", 5000)[:1000])
    age(incoming, seconds=7200)
    it.run()
    note = (incoming / intake.REVIEW_NAME / "Andy Weir - Artemis" / "WHY-NOT-FILED.txt").read_text()
    assert "upload it again" in note
    assert beets.calls == []


# --------------------------------------------------------------------- lock


def test_run_skips_while_another_holds_the_lock(env):
    it, incoming, library, beets = env
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")
    age(incoming)
    (incoming / intake.LOCK_NAME).write_text("other-pod pid=1")  # fresh: written after age()
    it.run()
    assert beets.calls == []
    assert (incoming / intake.LOCK_NAME).read_text() == "other-pod pid=1"  # not ours to remove


def test_stale_lock_is_broken(env):
    it, incoming, library, beets = env
    lock = incoming / intake.LOCK_NAME
    lock.write_text("dead-pod pid=1")
    os.utime(lock, (time.time() - 7200, time.time() - 7200))
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")
    age(incoming)
    it.run()
    assert it.filed == ["Andy Weir/Artemis"]
    assert not lock.exists()  # released after the run


def test_lock_released_even_if_run_raises(env, monkeypatch):
    it, incoming, library, beets = env
    monkeypatch.setattr(it, "recover", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        it.run()
    assert not (incoming / intake.LOCK_NAME).exists()


def test_stop_request_leaves_remaining_uploads(env):
    it, incoming, library, beets = env
    mp3(incoming / "Andy Weir - Artemis" / "a.mp3")
    mp3(incoming / "George Orwell - Animal Farm" / "a.mp3")
    age(incoming)
    original = it.process_book

    def process_then_stop(book):
        original(book)
        it.stop_requested = True

    it.process_book = process_then_stop
    it.run()
    assert len(it.filed) == 1
    assert len([p for p in incoming.iterdir() if not p.name.startswith((".", "_"))]) == 1


# --------------------------------------------------------------------- loop


def test_loop_heartbeat_only_after_healthy_pass(tmp_path):
    hb = tmp_path / "hb"
    results = iter([False, RuntimeError("boom"), True])

    def once():
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    seen = []
    intake.loop(once, interval=0, heartbeat=hb, should_stop=lambda: False,
                sleep=lambda s: None, max_iterations=2)
    seen.append(hb.exists())
    intake.loop(once, interval=0, heartbeat=hb, should_stop=lambda: False,
                sleep=lambda s: None, max_iterations=1)
    seen.append(hb.exists())
    assert seen == [False, True]  # unhealthy and raising passes never touch it


def test_loop_stops_promptly_during_sleep(tmp_path):
    calls, slept = [], []
    stop = {"now": False}

    def sleep(s):
        slept.append(s)
        stop["now"] = True  # SIGTERM arrives while sleeping

    intake.loop(lambda: calls.append(1) or True, interval=60, heartbeat=tmp_path / "hb",
                should_stop=lambda: stop["now"], sleep=sleep)
    assert calls == [1] and len(slept) == 1


def test_run_once_reports_unmounted_share(tmp_path):
    it = intake.Intake(tmp_path / "missing", tmp_path, FakeBeets(tmp_path), quiet_seconds=0)
    assert intake.run_once(it, verbose=False) is False


# ---------------------------------------------------- ASIN pin + language guard
#
# 2026-10-04: "Confessions of a Trash Droid [B0GWFGGR9J]" (English) was filed as
# the German edition B0HJZV1VGW. Its album tag "…The Complete Series in One" is a
# substring of the German title, and the ASIN in the name was stripped as noise.

TD_TAG = "Confessions of a Trash Droid: The Complete Series in One"


@pytest.mark.parametrize("name,expected", [
    ("Confessions of a Trash Droid [B0GWFGGR9J]", "B0GWFGGR9J"),
    ("Gideon the Ninth [1980004900]", "1980004900"),
    ("Some Book [123456789X]", "123456789X"),
    ("Some Book (B0GWFGGR9J)", None),      # brackets are required
    ("Some Book [2014]", None),            # a year is not an ASIN
    ("Some Book B0GWFGGR9J", None),
])
def test_asin_in_name(name, expected, tmp_path):
    d = tmp_path / name
    assert intake.find_asin(d, []) == expected


def test_asin_name_beats_tag_and_tag_is_fallback(tmp_path):
    d = tmp_path / "Book"
    f = mp3(d / "a.mp3")
    m = MediaFile(f); m.asin = "B0HJZV1VGW"; m.save()
    assert intake.find_asin(d, [f]) == "B0HJZV1VGW"
    assert intake.find_asin(tmp_path / "Book [B0GWFGGR9J]", [f]) == "B0GWFGGR9J"
    g = mp3(d / "b [1980004900].mp3")
    assert intake.find_asin(d, [g, f]) == "1980004900"  # a file name also beats a tag


def test_incident_replay_asin_in_name_pins_the_english_edition(env):
    it, incoming, library, beets = env
    mp3(incoming / "Confessions of a Trash Droid [B0GWFGGR9J]" / "book.mp3", album=TD_TAG, artist="Michael Cheney")
    age(incoming)
    it.run()
    assert it.filed == ["Michael Cheney/Confessions of a Trash Droid_ The Complete First Series in One"]
    assert beets.search_ids == ["B0GWFGGR9J"], "pinned only — the fuzzy search must never run"
    assert not any("German" in p.name for p in library.rglob("*"))


def test_incident_replay_without_asin_refuses_german_edition(env):
    it, incoming, library, beets = env
    d = incoming / "Confessions of a Trash Droid"
    f = mp3(d / "book.mp3", album=TD_TAG, artist="Michael Cheney")
    age(incoming)
    it.run()
    assert not any(library.iterdir()), "a foreign-language match must not reach the library"
    reviewed = incoming / intake.REVIEW_NAME / "Confessions of a Trash Droid"
    note = (reviewed / "WHY-NOT-FILED.txt").read_text()
    assert "German" in note and "ASIN" in note
    m = MediaFile(reviewed / "book.mp3")  # original name and tags restored, not the German ones
    assert (m.album, m.artist, m.language) == (TD_TAG, "Michael Cheney", None)
    assert not m.asin, "the wrong edition's ASIN must not survive, or a re-upload would pin it"
    assert not any(p for p in (incoming / intake.STAGING_NAME).rglob("*") if p.is_file())


def test_rejected_language_match_falls_through_to_next_attempt(env):
    it, incoming, library, beets = env
    mp3(incoming / "Michael Cheney - Confessions of a Trash Droid" / "book.mp3", album=TD_TAG, artist="Michael Cheney")
    age(incoming)
    it.run()
    assert it.filed == ["Michael Cheney/Confessions of a Trash Droid"]
    filed = next((library / "Michael Cheney" / "Confessions of a Trash Droid").glob("*.mp3"))
    assert MediaFile(filed).language == "English"
    assert beets.calls[0] == ("Michael Cheney", TD_TAG)  # German tried first, rejected
    assert beets.seen_asin == [None, None], "the next attempt must not inherit the rejected edition's tags"


def test_unstage_restores_names_and_tags_in_natural_order(env):
    it, incoming, library, beets = env
    d = incoming / "Confessions of a Trash Droid"
    names = ["1.mp3", "2.mp3", "10.mp3"]
    for i, n in enumerate(names):
        f = mp3(d / n, album=TD_TAG, artist="Michael Cheney")
        m = MediaFile(f); m.title = f"part {n}"; m.save()
    age(incoming)
    it.run()
    reviewed = incoming / intake.REVIEW_NAME / "Confessions of a Trash Droid"
    assert sorted(p.name for p in reviewed.glob("*.mp3")) == sorted(names)
    for n in names:
        assert MediaFile(reviewed / n).title == f"part {n}"  # each file back under its own name


def test_pinned_asin_that_does_not_fit_goes_to_review_without_searching(env):
    it, incoming, library, beets = env
    for n in ("a.mp3", "b.mp3"):  # ASINS says B0GWFGGR9J is one file
        mp3(incoming / "Confessions of a Trash Droid [B0GWFGGR9J]" / n, album="x", artist="y")
    age(incoming)
    it.run()
    assert beets.search_ids == ["B0GWFGGR9J"]
    note = (incoming / intake.REVIEW_NAME / "Confessions of a Trash Droid [B0GWFGGR9J]" / "WHY-NOT-FILED.txt").read_text()
    assert "did not fit" in note and "B0GWFGGR9J" in note
    m = MediaFile(incoming / intake.REVIEW_NAME / "Confessions of a Trash Droid [B0GWFGGR9J]" / "a.mp3")
    assert (m.album, m.artist) == ("x", "y")


def test_unknown_asin_falls_back_to_search(env):
    it, incoming, library, beets = env
    mp3(incoming / "Andy Weir - Artemis [B0ZZZZZZZZ]" / "a.mp3")
    age(incoming)
    it.run()
    assert it.filed == ["Andy Weir/Artemis"]
    assert beets.search_ids[0] is None


def test_explicit_asin_for_foreign_edition_is_honoured(env):
    it, incoming, library, beets = env
    mp3(incoming / "Trash Droid German [B0HJZV1VGW]" / "a.mp3")
    age(incoming)
    it.run()
    assert len(it.filed) == 1 and "German Edition" in it.filed[0]


def test_allowed_languages_is_configurable(env):
    it, incoming, library, beets = env
    it.allowed_languages = {"english", "german"}
    mp3(incoming / "Confessions of a Trash Droid" / "book.mp3", album=TD_TAG, artist="Michael Cheney")
    age(incoming)
    it.run()
    assert len(it.filed) == 1 and "German Edition" in it.filed[0]


def test_snapshot_round_trip_is_exact(tmp_path):
    tagged = mp3(tmp_path / "t.mp3", album="Orig", artist="Who")
    m = MediaFile(tagged); m.asin = "B0GWFGGR9J"; m.comments = "keep me"; m.save()
    bare = tmp_path / "bare.mp3"
    bare.write_bytes(MP3)  # no ID3 at all
    snap = intake.snapshot_tags([tagged, bare])
    for f in (tagged, bare):
        x = MediaFile(f)
        x.album, x.artist, x.asin, x.language, x.comments = "WRONG", "WRONG", "B0HJZV1VGW", "German", "junk"
        x.save()
    intake.restore_snapshot(snap)
    t = MediaFile(tagged)
    assert (t.album, t.artist, t.asin, t.comments, t.language) == ("Orig", "Who", "B0GWFGGR9J", "keep me", None)
    import mutagen
    assert mutagen.File(bare).tags is None, "a file uploaded without tags must go back to having none"

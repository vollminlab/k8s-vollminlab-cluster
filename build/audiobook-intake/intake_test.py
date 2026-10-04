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

KNOWN = {  # (artist, album) the fake Audible recognises -> canonical (author, title)
    ("Andy Weir", "Artemis"): ("Andy Weir", "Artemis"),
    ("George Orwell", "Animal Farm"): ("George Orwell", "Animal Farm"),
    ("Brandon Sanderson", "Mistborn"): ("Brandon Sanderson", "The Final Empire"),
}


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
    """Moves a book into staging iff its tags name a KNOWN book, like a
    confident beets-audible match; otherwise leaves it alone, like a skip."""

    def __init__(self, staging: Path):
        self.staging = staging
        self.calls: list[tuple[str, str]] = []

    def import_book(self, book: Path) -> None:
        files = intake.audio_files(book)
        m = MediaFile(files[0])
        key = (m.artist or "", m.album or "")
        self.calls.append(key)
        if key not in KNOWN:
            return
        author, title = KNOWN[key]
        dest = self.staging / author / title
        dest.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(files, 1):
            f.rename(dest / f"{i:02d} - {title}{f.suffix}")
        (dest / "cover.jpg").write_bytes(b"jpg")


@pytest.fixture
def env(tmp_path):
    incoming, library = tmp_path / "incoming", tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    (incoming / intake.STAGING_NAME).mkdir()
    beets = FakeBeets(incoming / intake.STAGING_NAME)
    it = intake.Intake(incoming, library, beets, quiet_seconds=900)
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

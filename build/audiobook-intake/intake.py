"""File audiobooks dropped into the incoming share into the Audiobookshelf library.

Friends upload through FileBrowser in any shape: a single file, a folder of
mp3s, CD1/CD2 subfolders, a zip, several books at once, tagged or not. This
normalises each upload into one folder per book, asks beets-audible to identify
it against Audible, and files it as <Author>/<Title>/ in the library.

Anything that cannot be identified confidently goes to _needs-review/ with its
original tags restored, so a wrong guess never reaches the library.
"""

import json
import logging
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from mediafile import MediaFile
import mutagen
from mutagen.mp3 import MP3
from natsort import os_sorted

log = logging.getLogger("intake")

AUDIO = {".mp3", ".m4b", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".wav"}
EBOOK = {".epub", ".pdf", ".mobi", ".azw3"}
UNSUPPORTED_ARCHIVE = {".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
DISC_DIR = re.compile(r"(?i)^(cd|dis[ck]|part|vol(ume)?)[\s._-]*\d+$")
# An Audible ASIN in a file or folder name, the way Libation, OpenAudible and most
# rippers write it: "[B0GWFGGR9J]", or ten digits/ISBN-style for older titles
# ("[1980004900]" is Gideon the Ninth). Square brackets required, so a year or a
# track number can never be mistaken for one.
ASIN_IN_NAME = re.compile(r"\[(B0[A-Z0-9]{8}|\d{9}[\dX])\]")
# Release noise that hurts the Audible query: "(Unabridged)", "[B002V5H6F4]", "(2014)".
NAME_NOISE = re.compile(r"(?i)\s*[\(\[]\s*(un)?abridged\s*[\)\]]|\s*\[[A-Z0-9]{10}\]|\s*\(\d{4}\)")

STAGING_NAME = ".intake-staging"
REVIEW_NAME = "_needs-review"
PARTIAL_SUFFIX = ".intake-partial"
LOCK_NAME = ".intake-lock"

MP4_FAMILY = {".m4b", ".m4a", ".mp4"}
# A complete mp3 measured 1.000 (VBR Xing and CBR Info headers alike) and a
# half-uploaded one 0.500, so 0.97 only leaves room for odd trailing tags.
MP3_COMPLETE_RATIO = 0.97

BEETS_CONFIG = """\
plugins: audible fromfilename scrub inline
directory: {staging}
library: {db}
item_fields:
  book_file: (f"{{track:02d}} - {{album}}" if tracktotal > 1 else album)
paths:
  default: $albumartist/$album/$book_file
  comp: $albumartist/$album/$book_file
  singleton: $albumartist/$album/$book_file
musicbrainz:
  enabled: no
import:
  move: yes
  write: yes
  quiet: yes
  quiet_fallback: skip
  resume: no
  incremental: no
match:
  # beets' default (0.04) rejects correct Audible matches whose title differs
  # cosmetically ("Mistborn: The Final Empire" vs Audible's "The Final Empire"
  # scored 0.11). Wrong books scored >= 0.23 in testing, so 0.15 keeps margin.
  strong_rec_thresh: {threshold}
audible:
  match_chapters: true
  data_source_mismatch_penalty: 0.0
  fetch_art: true
  include_narrator_in_artists: false
  write_description_file: true
  write_reader_file: true
  region: {region}
scrub:
  auto: yes
"""


# ---------------------------------------------------------------- helpers


def is_audio(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in AUDIO


def audio_files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if is_audio(p))


def is_junk(p: Path) -> bool:
    return p.name.startswith("._") or p.name == "__MACOSX" or p.name in {".DS_Store", "Thumbs.db"}


def newest_mtime(p: Path) -> float:
    newest = p.stat().st_mtime
    if p.is_dir():
        for c in p.rglob("*"):
            try:
                newest = max(newest, c.stat().st_mtime)
            except FileNotFoundError:
                pass
    return newest


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    n = 2
    while (candidate := p.with_name(f"{p.name} ({n})")).exists():
        n += 1
    return candidate


def clean_name(name: str) -> str:
    name = NAME_NOISE.sub("", name)
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip(" .-")


def copy_tree_plain(src: Path, dst: Path) -> None:
    """Copy file contents only. CIFS mounts with forced uid/mode reject the
    chmod/utime that shutil.copytree's copystat issues."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_dir():
            copy_tree_plain(item, dst / item.name)
        else:
            shutil.copyfile(item, dst / item.name)
            if (dst / item.name).stat().st_size != item.stat().st_size:
                raise OSError(f"size mismatch copying {item}")


# ------------------------------------------------------------ completeness
#
# FileBrowser's TUS upload writes into the final filename and grows it, so a
# half-uploaded book is a real-looking file under its real name. The quiet
# period catches a stalled upload only if it stays stalled for the whole period;
# these checks catch it structurally, however long the stall.


def mp4_boxes_complete(path: Path, size: int | None = None) -> bool:
    """Every top-level MP4 box declares its own length, so a truncated file is
    one whose last box ends past EOF — whether moov sits before or after mdat.
    `size` overrides the real length (for testing against a real file)."""
    size = path.stat().st_size if size is None else size
    off = 0
    with open(path, "rb") as f:
        while off < size:
            f.seek(off)
            head = f.read(16)
            if len(head) < 8:
                return False
            n = struct.unpack(">I", head[:4])[0]
            if n == 1:  # 64-bit "largesize" follows the type
                if len(head) < 16:
                    return False
                n = struct.unpack(">Q", head[8:16])[0]
            elif n == 0:  # box extends to EOF by definition
                return True
            if n < 8:
                return False  # not a valid box: corrupt or not MP4 at all
            off += n
    return off == size


def mp3_complete(path: Path) -> bool:
    """A VBR Xing or CBR Info header records the whole stream's length, so a
    truncated file is smaller than duration x bitrate predicts. Without such a
    header mutagen estimates the length FROM the size, the ratio is always ~1,
    and truncation is undetectable — those rely on the quiet period alone."""
    m = MP3(path)
    expected = m.info.length * m.info.bitrate / 8
    if not expected:
        return True
    tag_bytes = m.tags.size if m.tags is not None else 0
    return (path.stat().st_size - tag_bytes) / expected >= MP3_COMPLETE_RATIO


def file_complete(p: Path) -> bool:
    suffix = p.suffix.lower()
    try:
        if suffix in MP4_FAMILY:
            return mp4_boxes_complete(p)
        if suffix == ".mp3":
            return mp3_complete(p)
        if suffix == ".zip":
            return zipfile.is_zipfile(p)  # needs the end-of-archive directory
    except Exception as e:
        log.debug("cannot parse %s yet: %s", p, e)
        return False  # unparseable mid-upload is the common case; retry later
    return True


def incomplete_files(upload: Path) -> list[Path]:
    files = [upload] if upload.is_file() else sorted(p for p in upload.rglob("*") if p.is_file())
    return [p for p in files if not file_complete(p)]


# ---------------------------------------------------------- normalisation


def extract_zip(z: Path) -> Path:
    dest = unique_path(z.with_suffix(""))
    with zipfile.ZipFile(z) as f:
        for member in f.infolist():
            parts = Path(member.filename).parts
            if any(part == "__MACOSX" or part.startswith("._") for part in parts):
                continue
            if member.filename.startswith("/") or ".." in parts:
                raise ValueError(f"unsafe path in {z.name}: {member.filename}")
            f.extract(member, dest)
    z.unlink()
    inner = [p for p in dest.iterdir() if not is_junk(p)]
    if len(inner) == 1 and inner[0].is_dir():  # a zip of one folder: drop the extra level
        tmp = dest.with_name(dest.name + ".unwrap")
        inner[0].rename(tmp)
        shutil.rmtree(dest)
        tmp.rename(dest)
    return dest


def flatten_disc_dirs(book: Path) -> None:
    """CD1/Track 1.mp3 -> "CD1 - Track 1.mp3", so beets sees one album and
    natural sort keeps disc order."""
    for sub in sorted(s for s in book.iterdir() if s.is_dir() and DISC_DIR.match(s.name)):
        for f in sorted(sub.rglob("*")):
            if f.is_file():
                f.rename(unique_path(book / f"{sub.name} - {f.name}"))
        shutil.rmtree(sub)


def find_books(upload: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Return (book dirs, [(path, reason)] to send to review).

    A directory is a book if it holds audio directly or only disc subfolders.
    A directory holding only other directories is a container of books.
    """
    books, rejects = [], []
    for z in list(upload.rglob("*.zip")) if upload.is_dir() else []:
        try:
            extract_zip(z)
        except (zipfile.BadZipFile, ValueError, OSError) as e:
            rejects.append((z, f"could not extract zip: {e}"))

    def walk(d: Path) -> None:
        for j in [c for c in d.iterdir() if is_junk(c)]:
            shutil.rmtree(j) if j.is_dir() else j.unlink()
        archives = [c for c in d.iterdir() if c.is_file() and c.suffix.lower() in UNSUPPORTED_ARCHIVE]
        if archives:
            rejects.append((d, f"unsupported archive {archives[0].name} — please re-upload as .zip"))
            return
        flatten_disc_dirs(d)
        direct_audio = [c for c in d.iterdir() if is_audio(c)]
        subdirs = [c for c in d.iterdir() if c.is_dir()]
        if direct_audio and subdirs and any(audio_files(s) for s in subdirs):
            rejects.append((d, "mixes audio files with sub-folders of audio; put each book in its own folder"))
        elif direct_audio:
            books.append(d)
        elif subdirs:
            for s in sorted(subdirs):
                walk(s)
        elif not any(d.rglob("*")):
            shutil.rmtree(d)  # empty folder: nothing to do
        else:
            rejects.append((d, "no audio files found"))

    walk(upload)
    return books, rejects


def prepare_upload(upload: Path) -> Path | None:
    """Turn a top-level upload into a directory (wrapping loose files and
    extracting top-level zips). Returns None if the upload is not audio."""
    if upload.is_dir():
        return upload
    suffix = upload.suffix.lower()
    if suffix == ".zip":
        return extract_zip(upload)
    if suffix in AUDIO:
        d = unique_path(upload.with_name(clean_name(upload.stem) or "book"))
        d.mkdir()
        upload.rename(d / upload.name)
        return d
    return None


# ------------------------------------------------------------- tag seeding


def read_tags(files: list[Path]) -> dict[Path, tuple[str, str, str]]:
    out = {}
    for p in files:
        try:
            m = MediaFile(p)
            out[p] = (m.album or "", m.artist or "", m.albumartist or "")
        except Exception as e:  # unreadable file: beets will skip it too
            log.warning("cannot read tags of %s: %s", p, e)
            out[p] = ("", "", "")
    return out


def write_tags(files, album: str, artist: str) -> None:
    for p in files:
        m = MediaFile(p)
        m.album, m.artist, m.albumartist = album, artist, artist
        m.save()


def find_asin(book: Path, files: list[Path]) -> str | None:
    """The uploader's own statement of which edition this is. Names first: a
    name is what the uploader chose, while a tag may have been written by
    whatever tool (or earlier mis-match) last touched the file."""
    for name in [book.name, *(f.name for f in files)]:
        m = ASIN_IN_NAME.search(name)
        if m:
            return m.group(1)
    for f in files:
        try:
            tag = (MediaFile(f).asin or "").strip().upper()
        except Exception:
            continue
        if ASIN_IN_NAME.fullmatch(f"[{tag}]"):
            return tag
    return None


def lookup_asin(asin: str, region: str) -> tuple[str, str, str] | None:
    """(author, title, language) for an Audible ASIN, or None if Audible does
    not know it. Uses the same Audnexus endpoint beets-audible itself uses."""
    from beetsplug.api import get_book_info

    try:
        book, _ = get_book_info(asin, region)
    except Exception as e:
        log.info("ASIN %s not found on Audible (%s)", asin, e)
        return None
    author = ", ".join(a.name for a in book.authors) if book.authors else ""
    return author, book.title, book.language


def snapshot_tags(files: list[Path]) -> dict[Path, list | None]:
    """Every tag on every file, exactly as uploaded. A rejected beets import has
    already rewritten all of them — language, description, publisher, and the
    wrong edition's ASIN, which would make a re-upload pin the wrong book — so
    restoring only the fields we seeded is not enough."""
    snap = {}
    for p in files:
        try:
            m = mutagen.File(p)
        except Exception as e:
            log.warning("cannot snapshot tags of %s: %s", p, e)
            continue
        if m is None:
            continue
        if m.tags is None:
            snap[p] = None
            continue
        grouped: dict = {}
        for k, v in m.tags.items():  # Vorbis comments repeat keys; ID3/MP4 do not
            grouped.setdefault(k, []).append(v)
        snap[p] = [(k, vs) for k, vs in grouped.items()]
    return snap


def restore_snapshot(snap: dict[Path, list | None]) -> None:
    for p, items in snap.items():
        if not p.exists():
            continue
        m = mutagen.File(p)
        if items is None:
            if m.tags is not None:
                m.delete()
            continue
        if m.tags is None:
            m.add_tags()
        m.tags.clear()
        vorbis = isinstance(m.tags, mutagen._vorbis.VComment)
        for k, vs in items:
            # Vorbis: one key, many values. ID3 frames and MP4 atoms: unique keys.
            m.tags[k] = vs if vorbis else vs[0]
        m.save()


def seed_attempts(folder_name: str, original: dict) -> list[tuple[str, str]]:
    """(artist, album) pairs to try, most trustworthy first. None means "use
    the files' own tags unchanged"."""
    attempts: list = []
    albums = Counter(a for a, _, _ in original.values() if a)
    if albums:
        attempts.append(None)
    name = clean_name(folder_name)
    if " - " in name:
        # Folder names come both ways round ("Andy Weir - Artemis", "Animal
        # Farm - George Orwell"). The wrong order matches nothing on Audible,
        # so trying both is safe; seeding the whole name as a title is not
        # (it preferred a wrong book in testing).
        left, right = (s.strip() for s in name.split(" - ", 1))
        attempts += [(left, right), (right, left)]
    elif name:
        attempts.append(("", name))
    return attempts


# ------------------------------------------------------------------- beets


class Beets:
    def __init__(self, staging: Path, threshold: float, region: str):
        self.workdir = Path(tempfile.mkdtemp(prefix="beets-"))
        self.staging = staging
        self.config = self.workdir / "config.yaml"
        self.config.write_text(
            BEETS_CONFIG.format(staging=staging, db=self.workdir / "library.db", threshold=threshold, region=region)
        )

    def import_book(self, book: Path, *, search_id: str | None = None, threshold: float | None = None) -> None:
        # A fresh database per book: beets' duplicate detection only knows what
        # is in its own DB, which never includes the existing library, so a
        # carried-over DB would only make a re-upload "skip" for the wrong reason.
        (self.workdir / "library.db").unlink(missing_ok=True)
        env = {**os.environ, "BEETSDIR": str(self.workdir)}
        cmd = ["beet"]  # config.yaml is read from BEETSDIR
        if threshold is not None:
            overlay = self.workdir / "threshold.yaml"
            overlay.write_text(f"match:\n  strong_rec_thresh: {threshold}\n")
            cmd += ["-c", str(overlay)]
        cmd += ["import"]
        if search_id:
            # Only this Audible book is considered; beets-audible's album_for_id.
            cmd += ["--search-id", search_id]
        r = subprocess.run(cmd + [str(book)], env=env, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"beet exited {r.returncode}: {r.stderr.strip()[-500:]}")


# ------------------------------------------------------------------ intake


class Intake:
    def __init__(self, incoming: Path, library: Path, beets, *, quiet_seconds: int, dry_run: bool = False,
                 incomplete_giveup_seconds: int = 86400, lock_stale_seconds: int = 3600, on_progress=None,
                 allowed_languages: tuple[str, ...] = ("english",), pinned_threshold: float = 0.4,
                 lookup=None):
        self.incoming = incoming
        self.library = library
        self.staging = incoming / STAGING_NAME
        self.review = incoming / REVIEW_NAME
        self.lock = incoming / LOCK_NAME
        self.beets = beets
        self.quiet_seconds = quiet_seconds
        self.dry_run = dry_run
        self.incomplete_giveup_seconds = incomplete_giveup_seconds
        self.lock_stale_seconds = lock_stale_seconds
        self.on_progress = on_progress or (lambda: None)
        self.allowed_languages = {lang.strip().lower() for lang in allowed_languages if lang.strip()}
        self.pinned_threshold = pinned_threshold
        self.lookup = lookup or (lambda asin: lookup_asin(asin, "us"))
        self.stop_requested = False
        self.filed: list[str] = []
        self.reviewed: list[str] = []
        # Why each upload is being held back, so a loop polling every minute logs
        # the reason once rather than every pass.
        self._waiting: dict[str, str] = {}

    # -- lock
    #
    # Two runs must never work the same upload. A Deployment with Recreate makes
    # that unlikely, but a rollout racing the old CronJob, a manual Job, or a
    # replica bump would all break it, and the cost is a double-filed book. The
    # lock lives on the share itself because that is the only thing every
    # possible runner has in common. O_EXCL create is atomic on SMB.

    def acquire_lock(self) -> bool:
        me = f"{socket.gethostname()} pid={os.getpid()}"
        for _ in range(2):
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self.lock.stat().st_mtime
                    holder = self.lock.read_text().strip()
                except FileNotFoundError:
                    continue  # released between our create and our stat: try again
                if age < self.lock_stale_seconds:
                    log.info("another intake run holds the lock (%s, %ds old); skipping this pass", holder, age)
                    return False
                log.warning("breaking stale intake lock held by %s (%ds old)", holder, age)
                self.lock.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as f:
                f.write(me)
            return True
        return False

    def refresh_lock(self) -> None:
        try:
            os.utime(self.lock)
        except FileNotFoundError:
            pass
        self.on_progress()

    def release_lock(self) -> None:
        self.lock.unlink(missing_ok=True)

    # -- review

    def to_review(self, path: Path, reason: str, display_name: str | None = None, *, retryable: bool = True) -> None:
        self.review.mkdir(exist_ok=True)
        dest = unique_path(self.review / (display_name or path.name))
        path.rename(dest)
        note = dest / "WHY-NOT-FILED.txt" if dest.is_dir() else dest.with_name(dest.name + ".WHY-NOT-FILED.txt")
        hint = (
            "To retry: rename the folder to 'Author - Title' (e.g. 'Andy Weir - Artemis'),\n"
            "delete this file, and move the folder back up into the Audiobooks folder.\n"
            if retryable else
            "This book is already in the library, so nothing was changed. Delete this folder\n"
            "if it is the same book, or tell Scott if it is a better copy.\n"
        )
        note.write_text(f"{reason}\n\n{hint}")
        self.reviewed.append(f"{dest.name}: {reason}")
        log.info("review: %s — %s", dest.name, reason)

    # -- library

    def finalize_staged(self, extras: list[Path] = ()) -> bool:
        """Move whatever beets placed in staging into the library. Returns True
        if at least one book was filed."""
        filed = False
        for author in sorted(p for p in self.staging.iterdir() if p.is_dir()):
            for book in sorted(p for p in author.iterdir() if p.is_dir()):
                for e in extras:
                    shutil.move(str(e), book / e.name)
                dest = self.library / author.name / book.name
                if dest.exists():
                    self.to_review(book, f"already in the library as {author.name}/{book.name}",
                                   f"{author.name} - {book.name} (duplicate)", retryable=False)
                    continue
                partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
                shutil.rmtree(partial, ignore_errors=True)
                copy_tree_plain(book, partial)
                partial.rename(dest)
                shutil.rmtree(book)
                self.filed.append(f"{author.name}/{book.name}")
                log.info("filed: %s/%s", author.name, book.name)
                filed = True
            if not any(author.iterdir()):
                author.rmdir()
        return filed

    def recover(self) -> None:
        """Finish or undo whatever a killed run left behind."""
        for partial in self.library.glob(f"*/*{PARTIAL_SUFFIX}"):
            log.warning("removing interrupted copy %s", partial)
            shutil.rmtree(partial)
        if self.staging.exists() and any(self.staging.iterdir()):
            log.warning("finishing books left in staging by an earlier run")
            self.finalize_staged()

    # -- per book

    def staged_book(self) -> Path | None:
        books = [b for a in self.staging.iterdir() if a.is_dir() for b in a.iterdir() if b.is_dir()]
        return books[0] if len(books) == 1 else None

    def staged_language(self, staged: Path) -> str | None:
        for f in audio_files(staged):
            try:
                if lang := MediaFile(f).language:
                    return str(lang)
            except Exception:
                continue
        return None

    def unstage(self, staged: Path, originals: list[Path]) -> None:
        """Undo a beets import we will not accept: put the audio back under its
        original names (beets numbers tracks in natural-sort path order, which is
        the order `originals` is in) and drop the cover/desc beets fetched."""
        moved = os_sorted(audio_files(staged))  # "100 - x" must follow "99 - x"
        if len(moved) != len(originals):
            raise RuntimeError(f"cannot undo match: {len(moved)} staged files vs {len(originals)} originals")
        for src, dst in zip(moved, originals):
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        author = staged.parent
        shutil.rmtree(staged)
        if not any(author.iterdir()):
            author.rmdir()

    def _accept(self, book: Path, label: str) -> None:
        extras = [p for p in book.rglob("*") if p.is_file() and p.suffix.lower() in EBOOK]
        self.finalize_staged(extras)
        shutil.rmtree(book, ignore_errors=True)
        log.info("matched %s using %s", book.name, label)

    def process_book(self, book: Path) -> None:
        self.refresh_lock()
        files = os_sorted(audio_files(book))
        original = read_tags(files)
        snapshot = snapshot_tags(files)

        # 1. The uploader named the edition: use exactly that one, or nothing.
        #    2026-10-04: an upload named "[B0GWFGGR9J]" (English) was fuzzy-matched
        #    to the German edition B0HJZV1VGW, because its album tag was a
        #    substring of the German title. The ASIN was in the name all along.
        asin = find_asin(book, files)
        if asin:
            info = self.lookup(asin)
            if info:
                author, title, language = info
                write_tags(files, title, author)
                self.beets.import_book(book, search_id=asin, threshold=self.pinned_threshold)
                if not audio_files(book):
                    self._accept(book, f"ASIN {asin} ({title}, {language})")
                    return
                restore_snapshot(snapshot)
                self.to_review(book, f"the upload names Audible book {asin} ({title} by {author}), but its "
                                     "files did not fit that book (track count or lengths). Check the ASIN.")
                return
            log.info("ASIN %s in %s is not on Audible; falling back to search", asin, book.name)

        # 2. Search. Any match must also be in an allowed language, because a
        #    title search cannot tell editions apart and the audio cannot be
        #    checked — a foreign edition's metadata on English audio is a silent
        #    mislabel, the worst outcome this tool can have.
        rejected: list[str] = []
        for attempt in seed_attempts(book.name, original):
            if attempt is None:
                restore_snapshot(snapshot)
                label = "file tags"
            else:
                artist, album = attempt
                write_tags(files, album, artist)
                label = f"artist={artist!r} album={album!r}"
            self.beets.import_book(book)
            if not audio_files(book):
                staged = self.staged_book()
                lang = self.staged_language(staged) if staged else None
                if staged and lang and lang.lower() not in self.allowed_languages:
                    log.info("rejected %s match for %s using %s: %s edition", staged.name, book.name, label, lang)
                    rejected.append(f"{staged.parent.name} — {staged.name} ({lang})")
                    self.unstage(staged, files)
                    restore_snapshot(snapshot)
                    continue
                self._accept(book, label)
                return
            log.info("no confident match for %s using %s", book.name, label)
        restore_snapshot(snapshot)
        if rejected:
            allowed = "/".join(sorted(self.allowed_languages))
            self.to_review(book, f"the only matches were in another language: {'; '.join(rejected)}. "
                                 f"Only {allowed} editions are filed automatically. If one of those IS this "
                                 "book, add its Audible ASIN to the folder name, e.g. 'Author - Title [B0XXXXXXXX]'.")
        else:
            self.to_review(book, "no confident Audible match (author/title not recognised)")

    # -- run

    def _hold(self, p: Path, why: str) -> None:
        if self._waiting.get(p.name) != why:
            log.info("waiting: %s — %s", p.name, why)
        self._waiting[p.name] = why

    def uploads(self) -> list[Path]:
        now = time.time()
        out = []
        present = set()
        for p in sorted(self.incoming.iterdir()):
            if p.name.startswith((".", "_")) or is_junk(p):
                continue
            present.add(p.name)
            age = now - newest_mtime(p)
            if age < self.quiet_seconds:
                self._hold(p, f"changed in the last {self.quiet_seconds}s (upload in progress)")
                continue
            bad = incomplete_files(p)
            if bad:
                if age >= self.incomplete_giveup_seconds and not self.dry_run:
                    self._waiting.pop(p.name, None)
                    self.to_review(p, f"{bad[0].name} is incomplete and has not changed for {int(age // 3600)}h; "
                                      "the upload was probably interrupted. Please upload it again.")
                    continue
                self._hold(p, f"{bad[0].name} is incomplete (upload interrupted or still arriving)")
                continue
            self._waiting.pop(p.name, None)
            out.append(p)
        for gone in set(self._waiting) - present:
            del self._waiting[gone]
        return out

    def run(self) -> None:
        self.filed, self.reviewed = [], []
        if not self.acquire_lock():
            return
        try:
            self._run_locked()
        finally:
            self.release_lock()

    def _run_locked(self) -> None:
        self.staging.mkdir(exist_ok=True)
        self.recover()
        for upload in self.uploads():
            if self.stop_requested:
                log.info("stop requested; leaving remaining uploads for the next start")
                return
            self.refresh_lock()
            if self.dry_run:
                log.info("dry-run: would process %s", upload.name)
                continue
            try:
                d = prepare_upload(upload)
                if d is None:
                    self.to_review(upload, "not an audio file or .zip")
                    continue
                books, rejects = find_books(d)
                for path, reason in rejects:
                    if path.exists():
                        self.to_review(path, reason)
                for book in books:
                    try:
                        self.process_book(book)
                    except Exception as e:
                        log.exception("failed on %s", book)
                        if book.exists():
                            self.to_review(book, f"intake error: {e}")
                if d.exists() and not any(p for p in d.rglob("*") if p.is_file()):
                    shutil.rmtree(d)
            except Exception as e:
                log.exception("failed on upload %s", upload)
                if upload.exists():
                    self.to_review(upload, f"intake error: {e}")


# ---------------------------------------------------------- Audiobookshelf


def trigger_abs_scan(url: str, library_id: str, api_key: str) -> None:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/libraries/{library_id}/scan",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        log.info("audiobookshelf scan requested: HTTP %s", r.status)


def run_once(intake: "Intake", *, verbose: bool) -> bool:
    """One pass. Returns False when the shares are not usable, so the caller
    can withhold the heartbeat and let the liveness probe restart the pod."""
    for d in (intake.incoming, intake.library):
        if not d.is_dir():
            log.error("%s is not mounted", d)
            return False
    intake.run()
    if verbose or intake.filed or intake.reviewed:
        log.info("summary: filed=%d review=%d", len(intake.filed), len(intake.reviewed))
        print(json.dumps({"filed": intake.filed, "review": intake.reviewed}), flush=True)
    if intake.filed and os.environ.get("ABS_API_KEY"):
        try:
            trigger_abs_scan(os.environ["ABS_URL"], os.environ["ABS_LIBRARY_ID"], os.environ["ABS_API_KEY"])
        except Exception as e:
            # Non-fatal: Audiobookshelf's own scan still picks the books up.
            # Logged at ERROR so the AudiobookIntakeErrors alert sees it.
            log.error("audiobookshelf scan request failed: %s", e)
    return True


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    os.utime(path)


def loop(once, *, interval: int, heartbeat: Path, should_stop, sleep=time.sleep, max_iterations=None) -> None:
    """Run `once` every `interval` seconds until `should_stop()`. A pass that
    raises is logged and the loop carries on; the heartbeat is only touched
    after a healthy pass, so a wedged or unmounted pod gets restarted."""
    n = 0
    while not should_stop():
        try:
            healthy = once()
        except Exception:
            log.exception("intake pass failed")
            healthy = False
        if healthy:
            touch(heartbeat)
        n += 1
        if max_iterations is not None and n >= max_iterations:
            return
        for _ in range(interval):
            if should_stop():
                return
            sleep(1)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    incoming = Path(os.environ.get("INCOMING_DIR", "/incoming"))
    library = Path(os.environ.get("LIBRARY_DIR", "/audiobooks"))
    interval = int(os.environ.get("LOOP_INTERVAL_SECONDS", "0"))
    heartbeat = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/intake-heartbeat"))
    if not incoming.is_dir():
        log.error("%s is not mounted", incoming)
        return 1
    staging = incoming / STAGING_NAME
    staging.mkdir(exist_ok=True)
    region = os.environ.get("AUDIBLE_REGION", "us")
    beets = Beets(staging, float(os.environ.get("MATCH_THRESHOLD", "0.15")), region)
    intake = Intake(
        incoming, library, beets,
        quiet_seconds=int(os.environ.get("QUIET_SECONDS", "900")),
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        incomplete_giveup_seconds=int(os.environ.get("INCOMPLETE_GIVEUP_SECONDS", "86400")),
        on_progress=lambda: touch(heartbeat),
        allowed_languages=tuple(os.environ.get("ALLOWED_LANGUAGES", "english").split(",")),
        pinned_threshold=float(os.environ.get("PINNED_MATCH_THRESHOLD", "0.4")),
        lookup=lambda asin: lookup_asin(asin, region),
    )

    if interval <= 0:  # one-shot (CronJob / manual Job)
        return 0 if run_once(intake, verbose=True) else 1

    def request_stop(signum, _frame):
        log.info("received signal %d; finishing the current book and stopping", signum)
        intake.stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log.info("watching %s every %ds (quiet period %ds)", incoming, interval, intake.quiet_seconds)
    loop(lambda: run_once(intake, verbose=False), interval=interval, heartbeat=heartbeat,
         should_stop=lambda: intake.stop_requested)
    return 0


if __name__ == "__main__":
    sys.exit(main())

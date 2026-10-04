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
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from mediafile import MediaFile

log = logging.getLogger("intake")

AUDIO = {".mp3", ".m4b", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".wav"}
EBOOK = {".epub", ".pdf", ".mobi", ".azw3"}
UNSUPPORTED_ARCHIVE = {".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
DISC_DIR = re.compile(r"(?i)^(cd|dis[ck]|part|vol(ume)?)[\s._-]*\d+$")
# Release noise that hurts the Audible query: "(Unabridged)", "[B002V5H6F4]", "(2014)".
NAME_NOISE = re.compile(r"(?i)\s*[\(\[]\s*(un)?abridged\s*[\)\]]|\s*\[[A-Z0-9]{10}\]|\s*\(\d{4}\)")

STAGING_NAME = ".intake-staging"
REVIEW_NAME = "_needs-review"
PARTIAL_SUFFIX = ".intake-partial"

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


def restore_tags(original: dict[Path, tuple[str, str, str]]) -> None:
    for p, (album, artist, albumartist) in original.items():
        if p.exists():
            m = MediaFile(p)
            m.album, m.artist, m.albumartist = album or None, artist or None, albumartist or None
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

    def import_book(self, book: Path) -> None:
        # A fresh database per book: beets' duplicate detection only knows what
        # is in its own DB, which never includes the existing library, so a
        # carried-over DB would only make a re-upload "skip" for the wrong reason.
        (self.workdir / "library.db").unlink(missing_ok=True)
        env = {**os.environ, "BEETSDIR": str(self.workdir)}
        r = subprocess.run(
            ["beet", "import", str(book)],  # config.yaml is read from BEETSDIR
            env=env, capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0:
            raise RuntimeError(f"beet exited {r.returncode}: {r.stderr.strip()[-500:]}")


# ------------------------------------------------------------------ intake


class Intake:
    def __init__(self, incoming: Path, library: Path, beets, *, quiet_seconds: int, dry_run: bool = False):
        self.incoming = incoming
        self.library = library
        self.staging = incoming / STAGING_NAME
        self.review = incoming / REVIEW_NAME
        self.beets = beets
        self.quiet_seconds = quiet_seconds
        self.dry_run = dry_run
        self.filed: list[str] = []
        self.reviewed: list[str] = []

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

    def process_book(self, book: Path) -> None:
        files = audio_files(book)
        original = read_tags(files)
        for attempt in seed_attempts(book.name, original):
            if attempt is None:
                restore_tags(original)
                label = "file tags"
            else:
                artist, album = attempt
                write_tags(files, album, artist)
                label = f"artist={artist!r} album={album!r}"
            self.beets.import_book(book)
            if not audio_files(book):
                extras = [p for p in book.rglob("*") if p.is_file() and p.suffix.lower() in EBOOK]
                self.finalize_staged(extras)
                shutil.rmtree(book, ignore_errors=True)
                log.info("matched %s using %s", book.name, label)
                return
            log.info("no confident match for %s using %s", book.name, label)
        restore_tags({p: t for p, t in original.items() if p.exists()})
        self.to_review(book, "no confident Audible match (author/title not recognised)")

    # -- run

    def uploads(self) -> list[Path]:
        cutoff = time.time() - self.quiet_seconds
        out = []
        for p in sorted(self.incoming.iterdir()):
            if p.name.startswith((".", "_")) or is_junk(p):
                continue
            if newest_mtime(p) > cutoff:
                log.info("waiting: %s changed in the last %ds (upload may be in progress)", p.name, self.quiet_seconds)
                continue
            out.append(p)
        return out

    def run(self) -> None:
        self.staging.mkdir(exist_ok=True)
        self.recover()
        for upload in self.uploads():
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    incoming = Path(os.environ.get("INCOMING_DIR", "/incoming"))
    library = Path(os.environ.get("LIBRARY_DIR", "/audiobooks"))
    for d in (incoming, library):
        if not d.is_dir():
            log.error("%s is not mounted", d)
            return 1
    staging = incoming / STAGING_NAME
    staging.mkdir(exist_ok=True)
    beets = Beets(staging, float(os.environ.get("MATCH_THRESHOLD", "0.15")), os.environ.get("AUDIBLE_REGION", "us"))
    intake = Intake(
        incoming, library, beets,
        quiet_seconds=int(os.environ.get("QUIET_SECONDS", "900")),
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
    )
    intake.run()
    log.info("summary: filed=%d review=%d", len(intake.filed), len(intake.reviewed))
    print(json.dumps({"filed": intake.filed, "review": intake.reviewed}))

    if intake.filed and os.environ.get("ABS_API_KEY"):
        try:
            trigger_abs_scan(os.environ["ABS_URL"], os.environ["ABS_LIBRARY_ID"], os.environ["ABS_API_KEY"])
        except Exception as e:
            # Non-fatal: Audiobookshelf's own watcher or nightly scan still picks
            # the books up. Log the reason so a broken key isn't invisible.
            log.error("audiobookshelf scan request failed: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# audiobook-intake

Files audiobooks that friends upload through FileBrowser into the Audiobookshelf library.

- **Created:** 2026-10-04
- **Source:** `build/audiobook-intake/intake.py` (image), `clusters/vollminlab-cluster/mediastack/audiobook-intake/app/` (CronJob)

## What it does

Every 10 minutes the `audiobook-intake` CronJob in `mediastack` looks at the top level of the
`audiobooks-incoming` share, which is FileBrowser's `Audiobooks` folder. For each upload that has not
changed in 15 minutes:

1. **Normalise the shape.** Extract `.zip` files, put loose audio files into their own folder,
   flatten `CD1/`, `Disc 2/` and similar folders into the book folder, and split a folder that
   holds several book folders into one book each.
2. **Identify the book.** Run beets with the beets-audible plugin. It tries the files' own tags
   first, then the folder name as `Author - Title`, then as `Title - Author`. A match is accepted
   only if beets' distance is at most `MATCH_THRESHOLD` (0.15).
3. **File it.** beets retags the files, renames them after the book, and adds `cover.jpg`,
   `desc.txt` and `reader.txt` from Audible. The book is staged in `/incoming/.intake-staging/`,
   then copied to `/audiobooks/<Author>/<Title>/`. Any `.epub`/`.pdf` in the upload goes with it.
4. **Tell Audiobookshelf.** If anything was filed, the job asks Audiobookshelf to scan the
   Audiobooks library.

Anything else goes to `_needs-review/` in the incoming share, which friends can see in FileBrowser.
That covers no confident match, an unsupported archive, an unreadable file, and a book that is
already in the library. The original tags are restored and a `WHY-NOT-FILED.txt` explains what
happened.

## Fixing a book in `_needs-review`

- **No match:** rename the folder to `Author - Title` (for example `Andy Weir - Artemis`), delete
  `WHY-NOT-FILED.txt`, and move the folder back up into `Audiobooks/`. It is picked up 15 minutes
  later.
- **`(duplicate)`:** the library already has `<Author>/<Title>`. Nothing in the library was changed.
  Delete the review copy, or replace the library copy by hand if the upload is better.
- **Not on Audible at all** (a lecture series or a BBC drama, say): add a `metadata.yml` to the
  folder in the beets-audible format, with `title`, `authors`, `narrators`, `description`,
  `genres`, `releaseDate` and `publisher`. beets then skips Audible. See the
  [beets-audible README](https://github.com/Neurrone/beets-audible#importing-non-audible-content).

## Design decisions

**Why the job tags files from the folder name.** On its own, beets skipped every untagged upload
in testing, and it searched Audible for `CD1` when a book came as disc folders. Trying both name
orders is safe because the wrong order matches nothing on Audible. Treating the whole folder name
as a title was **not** safe: `Animal Farm - George Orwell` preferred "Learn it in a Day — Animal
Farm" (0.49) over the real book (0.58).

**Why 0.15 and not beets' default 0.04.** Correct matches with cosmetically different titles
scored up to 0.11 ("Mistborn: The Final Empire" against Audible's "The Final Empire"). The nearest
wrong book seen in testing scored 0.23 ("Artemis Fowl" for "Artemis"). Do not raise the threshold
past about 0.2 without re-testing: a book that is not on Audible will then match a lookalike.

**Why staging lives on the incoming share.** beets' move is then a same-share rename, so a book is
copied over the network exactly once, into the library. Staging in an emptyDir would put whole
audiobooks on node disk.

**Why a fresh beets database for every book.** beets' duplicate detection only knows its own
database, which never contains the existing library. The job does its own duplicate check: does
`<Author>/<Title>` already exist?

**Why root.** The incoming share is mounted with uid 1000 and the library with uid 568, both mode
0755, and the job must delete from one and write to the other. The container drops every capability
except `DAC_OVERRIDE` and `FOWNER`. `mediastack` enforces PodSecurity `restricted` only in
warn/audit mode, so this produces a warning, not a rejection.

**Why the job waits 15 minutes.** FileBrowser's TUS upload writes chunks into the final filename
in place, so a half-uploaded file looks like a short, valid book.

## Operating it

```bash
# Recent runs and their exit codes
kubectl get jobs -n mediastack -l app=audiobook-intake --sort-by=.metadata.creationTimestamp

# What the last run filed / reviewed (the last line is a JSON summary)
kubectl logs -n mediastack -l app=audiobook-intake --tail=50

# Run now instead of waiting for the schedule
kubectl create job -n mediastack --from=cronjob/audiobook-intake intake-manual-$(date +%s)

# Look at the review folder
kubectl exec -n mediastack deploy/filebrowser -- ls -la /srv/Audiobooks/_needs-review
```

`DRY_RUN=true` on the CronJob logs which uploads would be processed without touching anything.

If a run is killed mid-copy, the next run deletes the partial `<Title>.intake-partial` folder and
finishes any book left in `.intake-staging`.

## Releasing a new image

Edit `build/audiobook-intake/`. The `Build In-House Images` CI job builds it and runs
`intake_test.py`. After merge, push a tag, then bump the tag in `cronjob.yaml` in a follow-up PR:

```bash
git tag audiobook-intake/v0.1.1 && git push origin audiobook-intake/v0.1.1
```

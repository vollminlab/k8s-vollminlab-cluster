# audiobook-intake

Files audiobooks that friends upload through FileBrowser into the Audiobookshelf library.

- **Created:** 2026-10-04
- **Source:** `build/audiobook-intake/intake.py` (image), `clusters/vollminlab-cluster/mediastack/audiobook-intake/app/` (Deployment)

## What it does

The `audiobook-intake` Deployment in `mediastack` checks the top level of the `audiobooks-incoming`
share every 60 seconds. That share is FileBrowser's `Audiobooks` folder. An upload is processed once
it is **complete** and **quiet**:

- **Complete:** every file passes a structural check, so a half-uploaded file is held rather than
  filed. See *Why it checks completeness* below.
- **Quiet:** nothing in it has changed for 5 minutes.

A book is usually in the library 5–6 minutes after its upload finishes. Each upload is then:

1. **Normalise the shape.** Extract `.zip` files, put loose audio files into their own folder,
   flatten `CD1/`, `Disc 2/` and similar folders into the book folder, and split a folder that
   holds several book folders into one book each.
2. **Identify the book.**
   - **If the upload names an Audible ASIN,** beets is pinned to exactly that book (`--search-id`)
     and nothing else is considered. The ASIN can be `[B0XXXXXXXX]` or `[1234567890]` in the
     folder name or a file name, or the file's own ASIN tag; a name beats a tag. If the files
     don't fit that book, the upload goes to review rather than being searched.
   - **Otherwise it searches.** Run beets with the beets-audible plugin, trying the files' own tags
     first, then the folder name as `Author - Title`, then as `Title - Author`. A match is
     accepted only if beets' distance is at most `MATCH_THRESHOLD` (0.15) **and** the matched
     edition's language is in `ALLOWED_LANGUAGES` (`english`). A match in another language is
     undone (original file names and every original tag restored) and the next attempt is tried.
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

- **Wrong edition, or "only matches were in another language":** add the book's Audible ASIN to
  the folder name, e.g. `Michael Cheney - Confessions of a Trash Droid [B0GWFGGR9J]`, and move it
  back up. The ASIN is in the book's Audible URL.
- **No match:** rename the folder to `Author - Title` (for example `Andy Weir - Artemis`), delete
  `WHY-NOT-FILED.txt`, and move the folder back up into `Audiobooks/`. It is picked up about 5 minutes
  later.
- **`(duplicate)`:** the library already has `<Author>/<Title>`. Nothing in the library was changed.
  Delete the review copy, or replace the library copy by hand if the upload is better.
- **Not on Audible at all** (a lecture series or a BBC drama, say): add a `metadata.yml` to the
  folder in the beets-audible format, with `title`, `authors`, `narrators`, `description`,
  `genres`, `releaseDate` and `publisher`. beets then skips Audible. See the
  [beets-audible README](https://github.com/Neurrone/beets-audible#importing-non-audible-content).

## Design decisions

**Why the ASIN wins, and why foreign-language matches are refused.** On 2026-10-04 an upload named
`Confessions of a Trash Droid [B0GWFGGR9J]`, an English book, was filed as the **German edition**
`B0HJZV1VGW`. The German listing's title, description, cover and `language: German` tag were all
written onto the English audio. The cause:

- The file's album tag was very likely *"…The Complete Series in One"*. beets-audible treats a
  candidate whose title **contains** the tag as a likely match. The German *"…The Complete Series
  in One (German Edition)"* does; the English *"…The Complete **First** Series in One"* does not.
- The exact ASIN was in the name, and the intake stripped it as noise.

A title search can't tell editions apart and nothing here can listen to the audio, so a
foreign edition's metadata on English audio is a silent mislabel, the worst outcome this tool can
have. Hence the two rules. When the uploader names the edition, it is used, even a foreign one;
that's the uploader's call. A search may only file an allowed language.

**Why a rejected match restores every tag, not just the ones the intake seeded.** beets has
already rewritten all of them by then, including the wrong edition's ASIN. Left in place, that
ASIN would make a re-upload pin the wrong book. Each file's complete tag set is snapshotted before
beets runs and restored exactly; verified byte-for-byte on a real m4b.

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

**Why it checks completeness, not just elapsed time.** FileBrowser's TUS upload writes chunks into
the final filename in place, so a half-uploaded file looks like a short, valid book. A quiet period
only catches a stalled upload if the stall lasts the whole period. A laptop that sleeps mid-upload
beats any timer. So each file must also pass a structural check:

| Format | Check | Measured |
|---|---|---|
| m4b / m4a / mp4 | every top-level box declares its length; a truncated file's last box ends past EOF | a real 963 MB m4b whose index (`moov`) sits after the audio: complete at 100 %, incomplete at 99.9 % |
| mp3 | the Xing/Info header's duration × bitrate vs the file size (minus ID3) | complete 1.000, half-uploaded 0.500 |
| zip | the central directory at the end of the archive is present | — |

An incomplete upload is **held, not reviewed**, and logged once (`waiting: <name> — <file> is
incomplete`). It goes to `_needs-review` with "please upload it again" only after 24 hours
unchanged (`INCOMPLETE_GIVEUP_SECONDS`).

**Known gap:** a CBR mp3 with **no** Xing/Info header can't be checked. mutagen derives its length
from the file size, so a half file reads as a short complete one. Those rely on the 5-minute quiet
period alone. Encoders have written the header by default for years, so this mostly means very old
rips.

**Why a Deployment and not a CronJob.** Polling every minute as a CronJob would create about 1,440
Jobs a day, and trivy-operator scans every Job it sees. That backlog once made worker04 do 83 % of
the cluster's writes. A single loop does the same work with no Job churn.

**Why there is a lock file.** Two runners must never work the same upload, or a book gets filed
twice. `replicas: 1` with `strategy: Recreate` makes an overlap unlikely. The lock covers what's
left: a manual Job, a replica bump, or a rollout racing an old pod. The lock is
`/incoming/.intake-lock`, an `O_EXCL` create (atomic on SMB) holding the holder's pod name. It is
refreshed before every book and broken if it is more than an hour stale.

## Operating it

```bash
# Is it running, and when did it last restart?
kubectl get pods -n mediastack -l app=audiobook-intake

# What it has been doing. Quiet passes log nothing; each pass that files or
# reviews something ends with a JSON summary line.
kubectl logs -n mediastack deploy/audiobook-intake --tail=50

# Look at the review folder
kubectl exec -n mediastack deploy/filebrowser -- ls -la /srv/Audiobooks/_needs-review

# Who holds the lock (normally absent between passes)
kubectl exec -n mediastack deploy/filebrowser -- cat /srv/Audiobooks/.intake-lock
```

There is nothing to trigger by hand: it checks every minute. To pause it, scale it to 0.
`DRY_RUN=true` logs which uploads would be processed without touching anything.

The **liveness probe** checks a heartbeat file. The heartbeat is touched after every healthy pass
and before every book, and **not** when a share is unmounted. If it is more than 40 minutes stale,
the pod is restarted.

On SIGTERM it finishes the current book and exits. If a pod is killed mid-copy anyway, the next one
deletes the partial `<Title>.intake-partial` folder and finishes any book left in
`.intake-staging`.

### Alerting

| Failure | Alert |
|---|---|
| The loop keeps crashing, or the liveness probe keeps restarting it | `KubePodCrashLooping` (generic) |
| No pod running / not ready | `KubeDeploymentReplicasMismatch`, `KubePodNotReady` (generic) |
| Image cannot be pulled | `KubeContainerWaiting` (generic, after 1 h) |
| A pass logged an `ERROR`: an exception on one upload, or the Audiobookshelf scan call failed | `AudiobookIntakeErrors` (Loki ruler, `loki-ruler-rules-configmap.yaml`) |

The last row exists because the intake carries on when a single book goes wrong, so one bad upload
can't block everyone else's. An ordinary "no confident match" or a held incomplete upload is
**not** an error. Those are logged at INFO and do not alert.

## Releasing a new image

Edit `build/audiobook-intake/`. The `Build In-House Images` CI job builds it and runs
`intake_test.py`. After merge, push a tag, then bump the tag in `deployment.yaml` in a follow-up PR:

```bash
git tag audiobook-intake/v0.2.1 && git push origin audiobook-intake/v0.2.1
```

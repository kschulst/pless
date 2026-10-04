# Importing a collection

You have a directory of documents and an empty archive. This is the slow part of setting up, and
most of what follows is about expectations rather than commands.

## Look before you upload

```console
$ pless docs scan ~/Documents/archive --hashes
Scanned 2,431 files in ~/Documents/archive

  2,180 supported            4.1 GB
    198 need conversion    612.0 MB
     53 unsupported         88.4 MB

  41 groups of identical files (112 files)
```

Three things are worth reading here.

**Files needing conversion are not imported.** Paperless consumes PDFs and images; a `.doc` or
`.odt` has to be converted first. `pless` reports them and leaves them alone rather than
pretending. Convert them yourself and scan again.

**Identical files are uploaded once.** The scan hashes every file, so copies of the same document
in different folders are recognised before anything is sent. You do not need to clean them up
first.

**Unsupported files stay where they are.** Archives, videos, and whatever else lives in a
documents folder after a decade. Nothing is deleted or moved — import only reads.

## Import

```console
$ pless docs upload ~/Documents/archive
Scanning /home/you/Documents/archive…
2180 to upload, 0 already uploaded and still being worked on, 0 already in the archive.
• 112 byte-identical to another file here, and will not be uploaded.
• 198 need conversion first, and will not be uploaded.
• 53 unsupported, and will not be uploaded.
Uploading to http://archive-01.tail.ts.net:8000. Paperless consumes in the background…

  Uploading 1/2180: 2019-invoice-january.pdf
  …
```

It needs **Tailscale up**, because Paperless listens on the machine's own interface only and
`pless` asks the target what it calls itself rather than guessing. It also needs
`PAPERLESS_API_TOKEN`, which you create in the Paperless UI under your user profile — documents
arrive owned by that user.

## The first run does not finish, and that is correct

Paperless OCRs documents one at a time. A few thousand documents is **days** of work on a
Raspberry Pi, and uploading is the fast part. So the command uploads, collects whatever has
already been consumed, and stops:

```console
✓ 340 document(s) in the archive (340 consumed, 0 already there).
• 1840 still being worked on. Run this again to collect them — nothing is lost in the meantime.
Manifest: /home/you/pless/pless-import.json
```

**Run it again** — tomorrow, or whenever. It offers what is not done, collects what has since
resolved, and stops again. That is both how you resume after an interruption and how you check on
progress; there is no separate command for either.

Use `--wait 0` to collect what is ready and return at once, without waiting.

## Why uploaded and consumed are counted separately

Because they are different facts, hours apart. Paperless accepting a file means it is queued, not
that it is in the archive — it may still turn out to be a duplicate or fail to parse. A tool that
reported the upload as success would be reporting the only thing you do not care about.

So every outcome comes from Paperless, and anything ambiguous counts as **not done**. A task
Paperless no longer lists stays pending rather than becoming a failure, because re-uploading on
that basis would duplicate a document the archive may already hold.

## The manifest

`pless-import.json`, beside `pless.toml`. It is keyed by content hash, so a file you later move or
rename is still recognised as done — and a file you *edit* is new content, and is offered again.

It is written after every upload, which is what makes interruption cheap: kill the command and you
lose at most one file's knowledge. Do not delete it. If it is unreadable, `pless` stops rather than
starting over, because starting over means re-uploading everything it accounted for.

## Failures

```console
✗ 3 could not be consumed. They are recorded and will not be retried unless you pass --retry-failed.
```

A document Paperless cannot parse will fail again tomorrow, so it is remembered rather than
retried — otherwise the one thing worth looking at is buried under every run. Look at the three,
fix or convert them, and then:

```console
$ pless docs upload --retry-failed
```

## Your nightly backup will skip while this runs

The exporter requires that nothing is being consumed, and an import keeps the queue busy for days.
So the nightly run **skips** rather than failing:

```console
$ pless backup status
• Last run 2026-10-04T02:14:03Z was skipped: Paperless was still consuming after 900s, so nothing
  was exported. An import in progress is the usual reason. Skipped 3 run(s) in a row.
```

This is deliberate. A failure every night during normal operation is an alert you learn to ignore,
and the alert you learn to ignore is the one that matters later.

It does not skip forever. After `[backup] max_busy_skips` consecutive skips — seven by default —
it becomes a failure, because by then an import is no longer a likely explanation and something is
stuck.

!!! warning "You have no off-site copy of what you are importing until the queue drains"

    Nothing is exported while the import runs, so the documents you have just added exist only on
    the target. Keep your original directory until `pless backup status` shows a successful run
    that includes them. Import reads your files and never moves or deletes them, so the originals
    are still there — do not tidy them away early.

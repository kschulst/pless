# 0012 — Import through the Paperless REST API, not the consume folder

- **Status:** Accepted
- **Date:** 2026-07-17
- **Status note:** decided; the import feature itself is not built yet

## Context

Paperless-ngx offers two ways in. The consume folder is the documented one: drop files in a
directory and they are ingested. The REST API takes an upload and returns a task identifier.

The consume folder looks simpler until the requirements are written down. Uploading a few
gigabytes needs resumability, duplicate detection, per-file status and a way to know when a
file has actually been consumed rather than merely copied. With a folder, all of that must be
inferred by watching files disappear — and a file that fails to parse simply stays there,
with no explanation attached to it.

## Decision

Documents are uploaded through `POST /api/documents/post_document/` with an API token. The
task identifier returned per file is what the local manifest records.

## Consequences

Duplicate detection, per-file status and consumption confirmation come from Paperless itself
rather than being reimplemented against a directory listing.

The manifest becomes simple: file, task, outcome. Resuming means retrying the files without a
successful outcome.

It requires the API to be reachable, which over Tailscale it is
([ADR 0004](0004-access-through-tailscale-only.md)), and an API token that only exists after
deployment.

Bulk first import of many gigabytes over the API is slower than an rsync into a folder. If
that proves painful in practice, rsync can be added as an explicit bulk path — but not as the
default, and not as the thing status is inferred from.

## Alternatives considered

- **rsync into the consume folder:** fast, and turns every status question into guesswork
  about vanishing files.
- **Both from the start:** two code paths making the same promises before either has a user.

# Git Workflow for the Paper Library

## Purpose

Record every completed paper ingestion and meaningful library maintenance as a deterministic Git commit. Git is an audit trail, not a reason to create noise.

## Repository Boundaries

- `E:/parper` is the paper-data repository. Commit papers, feature cards, analyses, extracted text, `INDEX.md`, and `index.json` here.
- The skill source repository is `C:/Users/df789/.workbuddy/skills/paper-library`. Commit changes to skill behavior separately there.
- The installed skill at `C:/Users/df789/.codex/skills/paper-library` is synchronized from the source and normally has no `.git` directory.
- Never push to a remote unless the user explicitly asks.

## When to Commit

Commit only after all of these are true:

1. The semantic work is complete: required frontmatter fields are filled, `README.md` and `analysis.md` are no longer scaffolds, and `status` is `summarized`.
2. `python scripts/library.py reindex` completed successfully.
3. `git status --porcelain` shows an actual change in the scoped paths.
4. `git diff --check` reports no whitespace error.

Do not commit after read-only retrieval or search. Do not create an empty commit.

## Ingest Commit

Use the paper directory name as the stable paper identifier. Replace the example `dualBrep` with the actual directory name.

```powershell
$PaperId = "dualBrep"
$PaperDirectory = "dualBrep"

git status --porcelain -- $PaperDirectory INDEX.md index.json
git diff --check -- $PaperDirectory INDEX.md index.json
git add -- $PaperDirectory INDEX.md index.json
git commit -m "paper-library: ingest $PaperId"
git log -1 --oneline
```

The commit must include the whole new paper directory and both generated indexes.

## Maintenance Commit

For a feature-card update, stage the card and both indexes:

```powershell
$PaperId = "dualBrep"
$PaperDirectory = "dualBrep"

git status --porcelain -- "$PaperDirectory/README.md" INDEX.md index.json
git diff --check -- "$PaperDirectory/README.md" INDEX.md index.json
git add -- "$PaperDirectory/README.md" INDEX.md index.json
git commit -m "paper-library: update card $PaperId"
git log -1 --oneline
```

If `analysis.md` also changed, add that file explicitly in both the `git status` and `git add` path lists. If only `analysis.md` changed, use `paper-library: update analysis <PaperId>`.

## Staging Rules

- Never use `git add -A`, `git add .`, or `git add --all`.
- Stage only the affected paper directory or named files, plus `INDEX.md` and `index.json`.
- Do not stage `_inbox/`, `.workbuddy/`, caches, local metadata, or another paper's uncommitted edits.
- If scoped status is empty after successful reindex, report that no commit was needed.
- If Git is missing or `E:/parper` is not a repository, report that limitation and continue the paper work unless the user asked to repair Git.

## Verification

After committing, verify all of these:

```powershell
git status --short
git log -1 --oneline
```

Expected result: the latest commit has the intended `paper-library:` message, and `git status --short` shows no unstaged scoped changes.

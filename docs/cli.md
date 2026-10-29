# Command line

The `second-brain` command calls the same local HTTP backend as the menu bar app. Open the app or run `make backend` first.

To list every command and its options, from the repository root run:

```bash
uv run second-brain --help
```

## Common commands

Bulk import a ChatGPT export:

```bash
uv run second-brain ingest --chatgpt ~/Downloads/chatgpt-export/conversations.json
```

Build or update the wiki from everything staged:

```bash
uv run second-brain compile
```

Remove a queued input or parsed source (see [deletion](lifecycle.md#deletion) for what each removal keeps):

```bash
uv run second-brain forget-drop ~/second-brain/drops/documents/notes.md
uv run second-brain forget documents/notes.md
```

Requeue sources for the next build, whether already compiled or set aside:

```bash
uv run second-brain recompile documents/notes.md
```

Connect the wiki to an assistant over MCP:

```bash
uv run second-brain mcp install --target claude-desktop
uv run second-brain mcp install --target chatgpt-desktop
uv run second-brain mcp install --target cursor
```

Ingest any modified or new files in the `drops` folder or any watched folder:

```bash
uv run second-brain ingest
```

Ingestion and build commands return a queued job immediately. Inspect its outcome with `status` or its job ID:

```bash
uv run second-brain status
uv run second-brain jobs show JOB_ID
uv run second-brain jobs cancel JOB_ID
uv run second-brain jobs retry JOB_ID
```

## Wiki maintenance

The wiki is its own git repository, committed to as each build progresses (see [how the wiki is versioned](lifecycle.md#how-the-wiki-is-versioned)). The `wiki` group inspects and repairs it while keeping the manifest, views, and search index in step.

View the `N` most recent build commits:

```bash
uv run second-brain wiki log -n <N>
```

Undo the last `N` build commits, requeueing their sources for compilation. This command will prompt before resetting (pass `--yes` to skip) and refuses to run on a dirty wiki tree.

```bash
uv run second-brain wiki rollback <N>
```

Detect pages renamed outside the pipeline (e.g. manual renames in Finder or from a script) and repoint every link that targeted the old name. Note that renaming files inside of Obsidian will automatically rewrite links and won't need any repair. Run without `--dry-run` to apply:

```bash
uv run second-brain wiki repair-links --dry-run
```

Retire duplicate pages into the one worth keeping. Move any prose worth saving into the destination first; the merge carries over sources and relationships, not body text:

```bash
uv run second-brain wiki merge <dest> <source 1> <source 2> ...
```

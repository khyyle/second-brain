# Command line

The menu bar app shells out to the `second-brain` command, so most of its operations are available from the terminal. This is useful for the first bulk import, scripting, and debugging. Run commands from the repository root.

To list every command and its options:

```bash
uv run second-brain --help
```

## Common commands

Bulk import a ChatGPT export:

```bash
uv run second-brain ingest --chatgpt ~/Downloads/chatgpt-export
```

Build or update the wiki from everything staged:

```bash
uv run second-brain compile
```

Remove a source from the pipeline, either mid-ingest or already staged (see [deletion](lifecycle.md#deletion) for what each removal keeps):

```bash
uv run second-brain forget-drop <path>    # a file still in drops/
uv run second-brain forget <raw-path>     # a staged source, forgotten entirely
```

Requeue sources for the next build, whether already compiled or set aside:

```bash
uv run second-brain recompile <raw-path>...
```

Connect the wiki to an assistant over MCP:

```bash
uv run second-brain mcp install --target claude-desktop   # or: cursor
```

Run the whole pipeline unattended on a schedule (8am, 2pm, and 8pm by default) on watched directories:

```bash
uv run second-brain schedule install
```

## Wiki maintenance

The wiki is its own git repository, committed to as each build progresses (see [how the wiki is versioned](lifecycle.md#how-the-wiki-is-versioned)). The `wiki` group inspects and repairs it while keeping the manifest, views, and search index in step.

View the `N` most recent build commits:

```bash
uv run second-brain wiki log -n 3
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
uv run second-brain wiki merge <dest> <source 1>...
```

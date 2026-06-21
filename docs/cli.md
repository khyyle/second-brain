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

Connect the wiki to an assistant over MCP:

```bash
uv run second-brain mcp install --target claude-desktop   # or: cursor
```

Run the whole pipeline unattended on a schedule (8am, 2pm, and 8pm by default) on watched directories:

```bash
uv run second-brain schedule install
```

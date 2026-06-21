# Using the app

Second Brain runs as a menu bar app: click the icon and a small window drops down. It is a thin frontend over the files in your vault at `~/second-brain/`, not a store of its own.

## Getting material in

Two kinds of material go in, and they go in differently on purpose.

1. Documents (PDFs, Markdown, plain text, LaTeX) go on the drop zone at the top. Drop a file or a folder onto it, or click it to browse. Dropping copies a file in and starts parsing it locally. None of this costs anything.

2. Chat history can be added by clicking the "Import ChatGPT export" button just under the drop zone. The app supports entering individual `conversation-*.json` files or a full data export folder--should you provide the full folder, the app will parse it and extract only the relevant `conversation-*.json` files.

## The three tabs

The tabs follow your material through the pipeline.

**Ingest** is where parsing happens. A file appears here while it is being turned into Markdown, with a spinner and a running clock; once parsed, it leaves this tab. Anything that fails to parse stays behind with a Retry. This stage is mechanical and free.

**Chats** is the review desk for imported conversations. Chat history is noisy, so a small local model sorts each conversation into worthwhile, review, or skip as it comes in (see [architecture doc](architecture.md) for more details). A "Needs review" list shows the conversations the model was unsure about, each with Keep and Skip. Below it, "Recent" shows what was already decided. You can flip any of these decisions, skip something that slipped through, or restore something you set aside. It is highly recommended that you prune chats for redundancy whether through this review, manually, or through an agent of your choice (just tell Cursor, Claude Code, Codex, to prune ~/second-brain/drops/chatgpt/). Only ingested *chats* show up here.

**Build** is where the wiki gets made, and the only tab tied to spending money. It lists what is staged — everything ingested and kept, ready to compile--with a rough cost estimate, and below that a log of pages already built.

## Building the wiki

A build reads your staged sources and writes wiki pages with a Claude agent. The number on the Build tab is a rough estimate based on known input/output costs. Once the a build is run, the real cost will ticks in the status line.

Before building a large pile of chats, it is worth grouping them first. Many conversations cover the same ground, and compiling each alone pays to write near-duplicate pages. "Group" (it appears on the Build tab once chats are staged) bundles related conversations so a topic compiles into one page instead of many. Grouping is local and free, and it shows its progress as it runs.

Once a grouping exists, the Build tab shows it. Each cluster is an expandable row: open it to see the conversations inside, split it back apart if the grouping reached too far, or pop a single conversation out to compile on its own. Conversations that didn't cluster with anything sit behind a collapsible "ungrouped" count, since each just becomes its own page. Stage or remove sources after grouping and the plan goes "out of date"--a "Regroup" recomputes it against what is staged now.

When the plan looks right, "Build wiki" compiles it. You can "Stop" mid-build: pages finished so far are kept, and the conversation in progress is rolled back cleanly so the next build redoes it from scratch.

## Reading what you built

The wiki is plain Markdown under `~/second-brain/wiki/`, so the natural way to read it is to open that folder as an Obsidian vault. "Reveal" in the status line opens the vault in Finder.

For asking questions instead of browsing, Second Brain ships an MCP server that hands the wiki to an assistant like Claude Desktop or Cursor, with keyword and semantic search over your pages. Settings has one-click buttons to connect each.

## Settings

The gear opens Settings. Here you can configure your Anthropic API key, a per-build spend cap, the handwriting parser, triage and search options, the folders to watch, and scheduling. You will need to set your Anthropic API key here before you can start building the wiki.

### Running on a schedule

By default the app only acts when you do: dropped files ingest on their own (free and local), and a wiki is built only when you press Build wiki. Turning on "Run automatically" (in Settings, under Automation) instead runs the pipeline on a timer, installing a macOS LaunchAgent (`com.secondbrain.pipeline`, via `launchd`) that ingests and builds at the times you set--the same work as dropping files and pressing Build wiki, but unattended.

Automation can also watch folders you choose. The app remembers what it has already ingested, so a watched folder is one you can just keep adding to: point it at a folder--say one where you continually drop research papers--and each scheduled run pulls in whatever is new and leaves the rest alone. It only ever reads from a watched folder; your originals are never moved or deleted, only copied and parsed into your vault. These folders are swept on scheduled runs, alongside the `~/second-brain/drops/` folders.

Two things to know about scheduled runs:

- They include the build, so they call Claude and cost money. Your per-build spend cap still applies, so a run won't exceed it.
- The agent runs even when the app is closed and survives logout and restart, but only if your computer is open and logged in. A run missed because the Mac was asleep happens on the next wake.

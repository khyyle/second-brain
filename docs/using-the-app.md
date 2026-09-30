# Using the app

Second Brain runs as a menu bar app: click the icon and a small window drops down. From here you can:
- **Ingest files**: drag and drop or select files to *stage* them for building
- **Build the wiki**: compile *staged* files into the wiki
- **Manage domains**: edit, delete, and merge tags used to denote the topic of a page
- **Review wiki health**: track unwritten concepts, unlinked pages, duplicates, and formatting issues

## Getting material in

Two kinds of sources can be ingested:

1. Documents (PDFs, Markdown, plain text, LaTeX) go on the drop zone at the top of the Ingest tab. Drop a file or a folder onto it, or click it to browse. You can also drop files anywhere on the window from the other tabs. Dropping copies a file in and starts parsing it locally. None of this costs anything.

2. Chat history can be added by clicking the "Import ChatGPT export" button just under the drop zone. The app supports individual `conversation-*.json` files or a full data export folder. If you provide the full folder, the app extracts only the conversation files it needs.

## The tabs

**Ingest** turns raw source files into Markdown, staging them for compilation into the actual wiki. Files that fail to parse stay behind so you can retry or remove them. Ingestion runs locally and is free.

**Chats** is a review desk for imported ChatGPT conversations. Chat history is noisy, so a small local model sorts each conversation into kept, review, or skip before anything reaches the build (see [architecture](architecture.md)). Conversations the model was unsure about wait in "Needs review" for your call, while "Recent" lets you flip past decisions or restore a chat you set aside. 

It is highly recommended that you prune chats for redundancy avoid paying to compile overlapping material, whether through this review, manually, or through an agent of your choice (just tell an agent of your choice to prune `~/second-brain/drops/chatgpt/` to your preferences).

**Build** is where staged sources are compiled into wiki pages using a cloud model. It shows what is ready to build with an estimated cost, along with a log of recently built pages. Removing a staged source takes it out of the pipeline entirely (see [deletion](lifecycle.md#deletion)).

**Domains** curates the broad subject tags used in page frontmatter. Renaming, merging, or deleting a domain rewrites every affected page's frontmatter so tag vocabulary stays consistent across the wiki.

**Overview** surfaces structural opportunities for wiki improvement and defect pages.

## Building the wiki

A build reads your staged sources and writes wiki pages with a cloud model agent. The number on the Build tab is a rough estimate based on known input/output costs. Once a build is running, the real cost will increment in the status line.

When the plan looks right, "Build wiki" compiles it. You can "Stop" mid-build: pages finished so far are kept, and the conversation in progress is rolled back cleanly so the next build redoes it from scratch.

If a source can't be compiled cleanly (e.g., the agent fails to finish compilation, or the source is too large for the model to read in one pass), it will be set aside with its relevant wiki edits rolled back. Failed sources are marked "Set aside" along with the reason, and can be recompiled via a retry button. Retried files will be marked ready for the next compilation pass. Importantly, sources that are too large to compile *cannot be retried* and must be split into smaller files first.

## Reading what you built

The wiki is stored in plain Markdown under `~/second-brain/wiki/` and opens directly as an Obsidian vault for visualization and traversal.

For asking questions instead of browsing, you can hand the wiki to Claude Desktop, ChatGPT Desktop, or Cursor over MCP. See [Querying over MCP](mcp.md) for what it does and how to set it up.

## Settings

You must set your compilation provider's API key in Settings before you can build the wiki. Settings also holds the compilation model, a per-build spend cap, triage tuning, and watched folders.

### Running on a schedule

By default the app only acts when you do: dropped files ingest on their own (free and local), and a wiki is built only when you press Build wiki. Turning on "Run automatically" (in Settings, under Automation) instead runs the pipeline on a timer, installing a macOS LaunchAgent (`com.secondbrain.pipeline`, via `launchd`) that ingests and builds at the times you set. It is the same work as dropping files and pressing Build wiki, but unattended.

Automation can also watch folders you choose. The app remembers what it has already ingested, so a watched folder is one you can keep adding to. Point it at a folder where you continually drop research papers, and each scheduled run pulls in whatever is new and leaves the rest alone. It only ever reads from a watched folder; your originals are never moved or deleted, only copied and parsed into your vault. These folders are swept on scheduled runs, alongside the `~/second-brain/drops/` folders.

Two things to know about scheduled runs:

- They include the build, so they call the cloud model and cost money. Your per-build spend cap still applies, so a run won't exceed it.
- The agent runs even when the app is closed and survives logout and restart, but only if your computer is open and logged in. A run missed because the Mac was asleep happens on the next wake.

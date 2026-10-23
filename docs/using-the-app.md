# Using the app

Second Brain runs as a menu bar app: click the icon and a small window drops down. From here you can:
- **Ingest files**: drag and drop or select files to *stage* them for building
- **Build the wiki**: compile *staged* files into the wiki
- **Manage domains**: edit, delete, and merge tags used to denote the topic of a page
- **Review wiki health**: track unwritten concepts, unlinked pages, duplicates, and formatting issues

## Getting material in

Two kinds of sources can be ingested:

1. Documents (PDFs, Markdown, plain text, LaTeX) go on the drop zone at the top of the Ingest tab. Drop a file or a folder onto it, or click it to browse. You can also drop files anywhere on the window from the other tabs. Dropping copies a file in and starts parsing it locally. None of this costs anything.

2. Chat history can be added by dropping a ChatGPT export on the same drop zone. The app supports individual `conversation-*.json` files or a full data export folder. If you provide the full folder, the app extracts only the conversation files it needs.

## The tabs

**Ingest** turns raw source files into Markdown, staging them for compilation into the actual wiki. Files that fail to parse stay behind, marked "Failed" along with the reason, so you can retry or remove them. Ingestion runs locally and is free.

Imported ChatGPT conversations get one more pass. Chat history is noisy, so a small local model sorts each conversation into kept, review, or skip before anything reaches the build (see [architecture](architecture.md)). Conversations the model was unsure about wait in "Needs review" at the top of the Ingest tab for your call, and a count on the tab shows how many are waiting.

It is highly recommended that you prune chats for redundancy avoid paying to compile overlapping material, whether through this review, manually, or through an agent of your choice (just tell an agent of your choice to prune `~/second-brain/drops/chatgpt/` to your preferences).

**Build** is where staged sources are compiled into wiki pages using a cloud model. It shows what is ready to build with an estimated cost, along with a log of recently built pages. A kept chat can still be skipped from the staged list, which moves it to the skipped chats on the Ingest tab. Removing a staged source takes it out of the pipeline entirely (see [deletion](lifecycle.md#deletion)).

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

New and changed files in watched folders ingest automatically while the app is open. Add folders in Settings under Automation; this works without enabling a schedule. Files are copied into `drops/<folder name>/` before parsing, and originals stay untouched. Already-ingested versions are left alone.

Files that fail stay visible in Ingest with a reason and Retry. The same failed version waits for Retry; editing the original makes it eligible on the next scan. You can also repair the queued copy and retry it without changing the original.

Turn on "Ingest on a schedule" to also scan while the app is closed. This installs a macOS LaunchAgent (`com.secondbrain.pipeline`, via `launchd`) that ingests at the times you set. It runs while you are logged in, and a run missed during sleep happens on wake.

Automatic runs only ingest and stage sources. Press Build wiki when you are ready to compile them.

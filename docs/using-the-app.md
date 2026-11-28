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

Review chats before building and skip redundant material so you don't pay to compile overlapping conversations.

**Build** is where staged sources are compiled into wiki pages using a cloud model. Choose the model at the top of the tab. Below it is what is ready to build with an estimated cost, along with a log of recently built pages. A kept chat can still be skipped from the staged list, which moves it to the skipped chats on the Ingest tab. Removing a staged source takes it out of the pipeline entirely (see [deletion](lifecycle.md#deletion)).

**Domains** manages the wiki's subject tags. Renaming, merging, or deleting a domain updates the tags on every affected page.

**Overview** lists unwritten topics, unlinked pages, possible duplicates, and pages that need titles, tags, or size adjustments.

## Building the wiki

A build reads your staged sources and writes wiki pages with a cloud model agent. The number on the Build tab is a rough estimate from the chosen model's prices. Once a build is running, what it has spent so far increments in the status line. The estimate reads "cost unknown" for a model with no known price, and the status line does once any turn's cost is unknown. The spend cap is checked after each model turn, so a build can pass it by one turn, and it cannot stop a build whose cost is unknown.

When the plan looks right, choose **Build wiki**. To stop mid-build, click **Stop** beside the build progress at the bottom of any tab. Changes from completed sources or groups are kept. Changes from the source or group in progress are discarded, and its sources stay staged for the next build.

If a source can't be compiled cleanly (e.g., the agent fails to finish compilation, or the source is too large for the model to read in one pass), it will be set aside and its unfinished wiki changes discarded. Failed sources are marked "Set aside" along with the reason, and can be recompiled via a retry button. Retried files will be marked ready for the next compilation pass. Sources that are too large to compile *cannot be retried* and must be split into smaller files first.

## Reading what you built

The wiki is stored in plain Markdown under `~/second-brain/wiki/` and opens directly as an Obsidian vault for visualization and traversal.

For asking questions instead of browsing, you can hand the wiki to Claude Desktop, ChatGPT Desktop, or Cursor over MCP. See [Querying over MCP](mcp.md) for what it does and how to set it up.

## Settings

Connect a model provider in Settings before you build the wiki: choose the provider, then **Sign in** or **Use API key**, depending on what it supports. Signing in takes effect immediately; click **Save** to keep the provider you chose. Settings also holds a per-build spend cap, triage tuning, and watched folders.

### Watched folders

New and changed files in watched folders ingest automatically while the app is open. Add folders in Settings under Watched folders. The app works on copies and leaves your originals untouched. Already-ingested versions are left alone.

Files that fail stay visible in Ingest with a reason and Retry. The same failed version waits for Retry; editing the original makes it eligible on the next scan. You can also repair the queued copy and retry it without changing the original.

Leave the app running to keep watching folders; closing its window is fine.

Automatic runs only ingest and stage sources. Press Build wiki when you are ready to compile them.

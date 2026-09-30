# Spec: failure visibility, GUI/CLI consistency, and a background agent

Status: draft for discussion
Baseline: commit `f53ef78` (2026-09-29). Line numbers refer to this commit.
Scope: the Python pipeline in `src/second_brain/`, the menu bar app in `gui/SecondBrainBar/`, and `run.sh`.

## Summary

The pipeline can fail without the user finding out. The clearest case is an API account running out of credits: every source fails, the build still finishes as if it succeeded, and the app returns to its idle footer. Several app surfaces also show stale or wrong information (the Recent list, parts of the Overview tab), the app and the CLI each keep their own copies of settings and constants, and automatic ingestion only happens while the app is open.

Sections 1 through 6 record each problem with evidence and a proposed fix that works in the current structure. Section 7 proposes a background agent process that owns ingestion and builds, with the app and CLI as its clients. Section 8 covers how tightly builds are tied to specific model providers, and section 9 covers UI text. The fixes in sections 1 through 6 should land first, since the agent would reuse that code.

## Implementation status

Update a row when its work lands. Code references in the sections below point at the baseline commit; Swift paths predate the GUI reorganization in `17462a6`, so look up files by type name instead.

| Item | Section | Phase | Status | Commit |
| --- | --- | --- | --- | --- |
| UI help text and wording | 9 | 0 | Done | `61c4886` |
| Overview titles carry their thresholds; duplicate explanations removed | 4.1 | 0 | Done (the Python descriptions weren't needed) | `61c4886` |
| Keep "Not linked from any page" as the label | 4.3, 10 | 0 | Decided, no change | |
| Drop from any tab, ⌘O, in-card drop confirmation | not in spec | 0 | Done | `de72858` |
| App guide wording | 9 | 0 | Done | `726d99b` |
| Re-dropped duplicates leave the drop queue | not in spec | 0 | Done | `f882402` |
| "Set aside" reason opens in a popover | not in spec | 0 | Done | `8ed6d1c` |
| GUI grouped by feature; `BuildLog` in its own file | 2 (part) | 0 | Done | `17462a6` |
| Model call behind an interface that reports error kinds | 8.5, 1.1 | 1 | Not started | |
| Account, rate-limit, and network errors stop the build; cleanup only after full success | 1.1 | 1 | Cleanup rule done, uncommitted; error stops not started | |
| Keep the cost spent before a mid-source error | 1.4 | 1 | Not started | |
| Run outcome record for ingest and build | 1.3 | 1 | Not started | |
| Exit codes 0 / 1 / 2, passed through `run.sh` | 1.8 | 1 | Not started | |
| Footer line for failed, partial, and capped runs | 1.3 | 1 | Not started | |
| Ingest with Ollama down reported as a failed run | 1.7 | 1 | Not started | |
| Ingest failure reasons stored and shown | 1.5 | 1 | Not started | |
| Failed watched-folder files listed | 1.6 | 1 | Not started | |
| Docs match the real failure behavior | 1.9 | 1 | Not started | |
| Current source in the heartbeat; spinner on its Build row | 3 | 1 | Not started | |
| Recent list written once per source, after commit | 2 | 1 | Not started | |
| Frontmatter guards in `write_page` and `edit_file` | 4.2 | 1 | Not started | |
| Gap names in the prompt; near-miss guard in `write_page` | 5 | 1 | Not started | |
| `catalog --json`; app drops its copied constants | 6.2 | 2 | Not started | |
| User config in the vault with validated writes | 6.3 | 2 | Not started | |
| Pipeline lock taken in Python | 6.4 | 2 | Not started | |
| Separate locks for ingest, triage, and build, so files dropped during a long run are parsed instead of skipped | 6.4 | 2 | Not started | |
| Ingest separated from build; per-folder auto-build | 6.5 | 3 | Not started | |
| Background agent and its API | 7 | 4 | Not started | |
| Provider adapters, OpenRouter, model evaluation | 8.2, 8.3, 8.5 | 5 | Not started | |
| Chats folded into Ingest and Build | not in spec | 0 | Done, uncommitted | |
| Chat re-imports matched by conversation id, not title; finalized skips not rewritten | not in spec | unscheduled, low priority | Proposed | |

## 1. Failures the user never sees



### 1.1 Account-level API errors are treated as per-source failures

The build loop catches every exception raised while compiling a source, logs it, rolls that source back, and moves on to the next one (`compilation/compiler.py:373-385`). An exhausted account returns HTTP 402 `billing_error`, and a rejected key returns 401. Neither is a problem with the source, so every remaining source fails the same way.

When the loop ends, the build counts as finished. Because the user didn't stop it, it empties `raw/.skipped/` and deletes the reviewed cluster plan (`compiler.py:424-429`). `compile` then prints "Sources compiled: 0" and exits 0.

Proposed change: classify API errors before deciding what the loop does next.

- Errors not caused by the source abort the build immediately. These are status 401, 402, 403, and 404 (a model id the account can't use), any 400 whose message mentions the credit balance (the form older accounts receive), and rate limits (429) or network failures that still fail after the SDK's own retries (it retries connection errors, 408, 409, 429, and 5xx twice by default). Every remaining source would fail the same way, so continuing only wastes time.
- Source-level errors keep today's behavior: the source is rolled back and the build moves on. These are "prompt is too long" and a run that ends without finishing.
- The purge of `raw/.skipped/` and the deletion of the reviewed cluster plan happen only after a build in which every source succeeded. A failed, partial, capped, or stopped build leaves both in place, and the run is recorded with a short reason (see 1.3).

Classify on `status_code`, not on the exception class. The installed SDK has no class for 402, so a billing error arrives as the base `anthropic.APIStatusError`. DeepSeek goes through the same client and also returns 402 for an empty balance. OpenRouter, if added (8.2), also returns 402 for a temporary spending limit that should be retried, so its 402s need a closer look.

Acceptance: with a client that raises a 402 on its first call, the loop stops after one attempt, `raw/.skipped/` and `.clusters.json` are untouched, the run record says "failed" with the reason "Out of API credits", and `compile` exits non-zero.

### 1.2 The app never learns how a run ended

The app starts builds and drop ingests with `PipelineRunner.runDetached` (`PipelineRunner.swift:20-33`), which discards stdout and stderr and never reads the exit status. `run.sh` swallows exit codes anyway (`run.sh:58-66`, `|| echo "Compilation failed"`). When a run ends, `clear_status` resets `.status.json` to idle (`status.py:108-120`) and the footer goes back to "N staged · M built" (`ContentView.swift:239-245`).

The only trace of a failure is `~/second-brain/logs/pipeline.log`.

### 1.3 Proposed: a run outcome record, shown in one place

Every ingest and compile run records how it ended in `~/second-brain/.last-run.json`, including when it ends with an exception. It belongs in the `finally` block that already calls `clear_status`. The file keeps one entry per stage, and a run replaces only its own stage's entry, so a successful ingest never hides a failed build. Writes are atomic (write a temporary file, then rename).

```json
{
  "ingest": {
    "outcome": "ok",
    "reason": "",
    "counts": {"completed": 1, "failed": 0},
    "started_at": "2026-09-29T23:05:02Z",
    "finished_at": "2026-09-29T23:05:09Z"
  },
  "compile": {
    "outcome": "failed",
    "reason": "Out of API credits",
    "counts": {"completed": 3, "failed": 9, "deferred": 1},
    "started_at": "2026-09-29T23:01:12Z",
    "finished_at": "2026-09-29T23:04:40Z"
  }
}
```

`outcome` is `ok`, `partial` (some sources failed or were set aside), `capped` (the spend cap stopped the build), `failed`, or `stopped` (the user pressed Stop). `reason` is one plain sentence, never a traceback. Python writes it, so the CLI and the app always say the same thing.

The app reads this file on the same one-second timer it already uses for `.status.json`. After a `partial`, `capped`, or `failed` run, the footer's idle text becomes one amber line, such as "Build failed: out of API credits", "Stopped at spend cap · 4 sources left", or "2 files failed to ingest". A `stopped` run shows no line, since the user asked for it. When both stages need attention, the footer shows the build's entry, since that is the one that costs money. Clicking the line opens the Ingest or Build tab. Each line stays until the user dismisses it or the next run of the same stage finishes. The app stores dismissals itself, keyed by stage and `finished_at`, so it never writes to the pipeline's file.

That is the guarantee: any run that doesn't fully succeed leaves a visible line until the user acknowledges it or the next run of that stage replaces it. There are no alerts and no notifications.

### 1.4 Money spent before a mid-source error drops out of the total

If `client.messages.create` raises partway through a source (`compiler.py:569`), the handler records `RunResult(0.0, FAILED)`, and the tokens already billed for that source are lost. The running total that the spend cap checks is then too low. `_run_agent` should catch API errors itself and return the cost accumulated so far along with the failed outcome.

### 1.5 Ingest failure reasons are thrown away

`Manifest.mark_failed` accepts an error message and only logs it (`ingestion/manifest.py:409-428`). The Ingest tab can show that a file failed, never why. Add an `error TEXT` column to the `manifest` table with a migration, store a one-line reason, and show it as the tooltip on Failed rows.

### 1.6 Failures in watched folders never appear

`VaultData.queue` only walks `drops/` (`VaultData.swift:47-79`). A watched-folder file that fails to parse shows up nowhere in the app. The run record covers the count, and the Ingest tab should also list failed watched-folder files from the manifest.

### 1.7 A whole ingest run can fail before touching any file

`ingest` checks Ollama first and raises if it isn't ready (`cli.py:78-89`). The Build button checks Ollama before it starts a build, but the ingest that runs after a drop doesn't. With Ollama off, dropped files sit in the Ingest tab as "waiting" indefinitely. With 1.3 in place, this becomes a failed run with the reason "Ollama isn't running".

### 1.8 CLI exit codes

`ingest` exits 0 when files fail, and `compile` exits 0 when sources fail. Both should exit 0 for `ok` or `stopped`, 1 for `failed`, and 2 for `partial` or `capped`, so scripts and scheduled runs can tell a total failure from a partial one. `run.sh` should pass the code through instead of echoing over it.

### 1.9 The docs describe behavior that doesn't exist

`docs/architecture.md:47` says a run that doesn't complete cleanly "is rolled back and set aside with a visible reason". That holds for sources that are too large or run out of turns, not for API errors. Update the doc when 1.1 and 1.3 land.

## 2. The Recent list on the Build tab

`WikiToolExecutor._record` (`compilation/agent.py:593-599`) is called from `_write`, `_set_page_meta`, and `_edit`. It immediately appends a line to `~/second-brain/.build-log.jsonl` through `build_log.append_build_actions`. The app parses that file in `BuildLog.recent`, which lives in `TriageData.swift:41-71`, and renders it in `BuildTab.recentSection` and `BuildRow` (`TabViews.swift:587-596` and `964-995`).

Problems:

- Rows for pages that were rolled back. The line is written as soon as the agent writes a page. If that source then fails, is stopped, runs out of turns, or hits the spend cap, `_git_restore` resets the wiki (`compiler.py:669-684`), but the line stays. Clicking the row tries to open a file that no longer exists, and nothing happens.
- Duplicate rows. A page touched by `write_page`, `set_page_meta`, and `edit_file` in the same run appears three times.
- No attribution. A row doesn't say which source produced it, although the executor has that list.
- Hard to find. The reader sits in a file named for triage.

Proposed change: the executor only collects changes. `_run_agent` returns them on `RunResult`, and the compiler calls `append_build_actions` once per source, in the `COMPLETED` branch right after `_git_commit`, writing one entry per page with a `sources` field. Move `BuildLog` and `BuildLogEntry` into `BuildLog.swift`, and show the sources in each row's tooltip.

Trade-off: rows appear when a source finishes rather than as each page is written. The in-progress row from section 3 makes up for it.

Acceptance: a stopped or failed source adds no lines, a page edited several times while compiling one source produces one line, and every line has a non-empty `sources` list.

## 3. Showing what is in progress

The Ingest tab already puts a small spinner on the file being processed, but the list is sorted by file modification time, newest first (`VaultData.swift:78`), so that row can sit anywhere. Sort processing rows first.

The Build tab shows "Building 3/12 · 1m 20s · $0.40" in the footer but doesn't mark which source that is, and one source can run for minutes. Add `current_sources` (raw paths) to the compile heartbeat written by `write_status`. The Build tab moves those rows to the top and swaps their dashed-circle icon for the same small spinner the Ingest tab uses. Both tabs then work the same way: one spinner on the active row, plus the count in the footer.

## 4. The Overview tab



### 4.1 Each check is explained twice, in two languages

The section "?" popovers (`TabViews.swift:1263-1283`) and the hover tooltips on each row (`TabViews.swift:1373-1382`) explain the same checks. The labels come from Python (`cli.py:924-966`), but both explanations and the 4000/150-word thresholds are hardcoded in Swift, copying `SPLIT_THRESHOLD` and `MERGE_THRESHOLD` from `wiki/health.py:69-71`. The names have already drifted: the app says "Stub pages" and the CLI summary says "Undersized pages".

Proposed change: `health --json` sends a `description` for each check, filling in thresholds from the Python constants. The app shows it in the section "?" popover, the pattern every other tab uses, and the Swift `explanation` switch goes away. The CLI text summary uses the same labels as the JSON.

### 4.2 Missing frontmatter can happen

`write_page` always writes `title` and `type`, but the tool schema doesn't require `domains` (`agent.py:326`), and an empty value is silently dropped (`agent.py:744-746`). `edit_file` does a plain find-and-replace over the whole file, so it can change or break the frontmatter block (`agent.py:845-881`). A manual edit or a YAML typo parses to an empty mapping, which flags all three fields at once.

Proposed change: make `domains` required and non-empty in `write_page`, and have `edit_file` reject any replacement that overlaps the frontmatter block, pointing the agent to `set_page_meta`. After that, the check only catches manual edits and broken YAML, so it should normally read zero. The check should also require a non-empty `domains` list instead of only testing that the key exists. Deleting a domain in the Domains tab leaves `domains: []`, which passes today.

### 4.3 The "Not linked from any page" label

This check means zero incoming links (`wiki/structure.py:339-356`). "Leaf node" would be the wrong name for it. In graph terms a leaf has no outgoing links, and these pages often link out a lot. The proposed label is "No backlinks", which is short and is the term Obsidian uses. The generated index page doesn't count as a link, so every new page starts in this list until another page links to it, and the description should say that.

## 5. Referenced-but-not-written topics and name drift

The compile prompt tells the agent to link topics that don't have a page yet, and says the link "resolves on its own once that page is built" (`agent.py:102-106`). Nothing ever schedules those gaps. A gap only closes if a later source covers the topic and the agent picks exactly the same name.

Two things keep names matching today. `slugify` normalizes case, punctuation, and accents. With `explore_tools` on, which is the default, the agent can call `list_gaps`, but nothing tells it to reuse a gap's name. The start sequence says to "search existing pages" (`agent.py:160-168`), and search can't find a gap because a gap has no page.

Here is the failure case. Page A links `[[conservation-of-energy]]`. A later source leads the agent to write "Conservation of Energy Theorem", which becomes `conservation-of-energy-theorem`. The gap stays forever, A's link still points nowhere, and the new page shows up under "No backlinks". The duplicate check can't connect the two, because the gap has no page to compare against.

Proposed changes, cheapest first:

1. Prompt. Add up to 30 of the most-referenced gaps to each run's task message, with an instruction to use the exact name when writing a page for one of them. This works even with `explore_tools` off.
2. A guard in `write_page`. Before creating a page, compare its slug to the current gap names. Split both into words, drop stopwords (of, the, a, an, and), and flag the pair when one word set contains the other. That catches `conservation-of-energy` against both `conservation-of-energy-theorem` and `energy-conservation`. On a match, return a soft error that names the gap and how many pages link to it, and let the agent pass `confirm_new: true` to go ahead anyway. This follows the existing "name is taken" guard.
3. An Overview action. Pair each gap with its closest written page, first by the same word-set test and then by embedding similarity, and offer "Point links here". That rewrites `[[gap]]` to `[[page|gap text]]` across the wiki, reusing the link-rewriting half of `wiki merge`. It also fixes drift from manual edits and MCP captures.

Not proposed: Obsidian `aliases`. Obsidian doesn't resolve a bare `[[alias]]` link to the file that declares the alias, so the vault would behave differently in Obsidian than in the app. Also not proposed yet: a paid "write this page" action for gaps. Revisit it once changes 1 through 3 show how much drift is left.

Acceptance for change 2: in a test wiki where two pages link the gap `conservation-of-energy`, `write_page` with the title "Conservation of Energy Theorem" returns the soft error, and the same call with `confirm_new: true` succeeds.

## 6. GUI and CLI consistency



### 6.1 The app reaches the pipeline four different ways

1. It spawns `run.sh` detached and discards the output (drop ingest and Build).
2. It spawns `uv run second-brain <command>` through a login shell for about fifteen commands: Ollama checks, schedule install, MCP install, cluster preview, recompile, forget, triage decisions, retry, domain edits, wiki merge and dismiss, health, and state.
3. It reads pipeline-owned files directly: `manifest.db` with hand-written SQL (`ManifestReader.swift`), plus `.status.json`, `.state.json`, `.build-log.jsonl`, and `.clusters.json`.
4. It writes pipeline-owned files directly: `config/config.yaml` through a line-based YAML editor (`ConfigStore.swift:94-172`), `.env`, `sources.json`, and `.cluster-overrides.json`. It also moves files into `raw/.skipped/` and the Trash.

Nothing defines a contract between the two sides, and that shows up in three ways:

- A schema change in Python can break the app silently. Every `ManifestReader` query returns an empty list when `sqlite3_prepare_v2` fails, so a renamed column shows up as an empty tab.
- Some actions can finish halfway. `ManifestMutator` moves the file to the Trash in Swift first, then runs the CLI cleanup without waiting for the result (`ManifestMutator.swift:14-23`). If the CLI step fails, the manifest keeps records for a file that is gone.
- Most command output is discarded. Only the MCP connect buttons read an error message back, through `runManagedResult`.

Speed is not the problem. On this machine `second-brain --help` takes about 0.15 s and `domain list --json` about 0.7 to 0.9 s, not counting login-shell startup.

### 6.2 Constants copied between Swift and Python


| Constant             | Python                        | Swift                                                                | Drift today                                                                     |
| -------------------- | ----------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Models and prices    | `llm_providers.py:22-47`      | `LLMProvider.swift:34-61`                                            | Switching to DeepSeek selects V4 Pro in the app; the Python default is V4 Flash |
| Default model        | `config.py:121`               | `ConfigStore.swift:25,50`, `TabViews.swift:480,611`                  | None yet                                                                        |
| Triage profiles      | `config.py:225`               | `ConfigStore.swift:13`, `SettingsView.swift:26-32`                   | None yet                                                                        |
| Wiki content folders | `wiki/structure.py:30`        | `TabViews.swift:1308`                                                | None yet                                                                        |
| Supported file types | `config.py:394`               | `VaultData.swift:44`, `SourcesStore.swift:19`, `DropStaging.swift:9` | The three Swift lists disagree with each other                                  |
| Vault location       | `config.py` (`data_dir`)      | `AppConfig.swift:18-25`                                              | The app always uses `~/second-brain` and ignores a custom `data_dir`            |
| Chat filename suffix | `ingestion/chatgpt_parser.py` | `TabViews.swift:17`                                                  | None yet                                                                        |
| Health thresholds    | `wiki/health.py:69-71`        | `TabViews.swift:1278-1279,1378-1379`                                 | None yet                                                                        |


Proposed change: add `second-brain catalog --json`. It emits the models (with prices, context windows, and each provider's default), the triage profiles with display names, the content folders, the supported file types, the resolved vault path, and the health thresholds. The app loads it once at launch and deletes its copies. In the agent design (section 7) this becomes an endpoint.

### 6.3 Settings live in a tracked file

The app writes user settings into `config/config.yaml`, which is committed to the repo. Personal choices, such as the current provider and triage profile, end up in git, and a `git pull` can conflict with them. Proposed change: keep `config/config.yaml` as the defaults and read user overrides from `~/second-brain/config.yaml`. Settings writes go through the CLI (`second-brain config set <key> <value>`), so Python validates them with the same pydantic model it loads them with. The Swift YAML line editor then goes away.

### 6.4 Only one entry point takes the pipeline lock

`run.sh` takes `~/second-brain/.pipeline.lock` (`run.sh:36-54`), and nothing else does. `recompile`, `forget`, `triage set`, the domain and wiki commands, and anything a user runs by hand can overlap a running build. The app disables its buttons only while a build runs (`PipelineStore.swift:33`), not during ingest. Proposed change: move the lock into Python as an `fcntl.flock` on a file in the vault, taken by every command that writes the manifest or the wiki, and have `run.sh` rely on that.

### 6.5 Automation mixes free work and paid work

One toggle, "Run automatically", controls both ingesting and building. The launchd job runs `run.sh` with no argument (`scheduler/launchd.py:57-60`), which is stage `all`: ingest everything, then build everything staged. That causes four problems:

- Watched folders can't be used without automatic spending. Only scheduled runs ingest them, and scheduled runs always build. The Watched folders list is hidden until the toggle is on (`SettingsView.swift:233-237`).
- A scheduled build compiles everything staged, including files the user dropped and was holding back, for example to review a grouping first.
- Watched folders are checked at fixed hours, not when a file arrives, so "put a file in a folder and it ends up in the wiki" only half works.
- The help has to explain that watched folders are processed "in addition to" drops. That distinction exists only because of how the schedule is built.

Proposed model: split the two decisions.

- Ingesting is free, so it happens automatically, on arrival, for every folder Second Brain watches. The drop folder becomes the built-in watched folder that the app copies files into. Watched folders then need no separate explanation: files placed in them are ingested and staged exactly like drops.
- Building costs money, so it stays manual by default. Each watched folder gets a "Build automatically" switch. Scheduled builds compile only staged sources from folders with the switch on, within the spend cap. Drops and ChatGPT imports stay manual; chats also have a review step that shouldn't be skipped.
- The schedule governs only automatic builds. Building in batches at set times, rather than as each file arrives, lets related files be grouped and gives the user a window to remove something.

Implementation notes: `WatchedFolder` gains `auto_build` in `sources.json` (`SourcesStore.swift:6-13`). Ingested output already lands in `raw/<folder name>/` (`cli.py:315-337`), so a staged source maps back to its folder by path. `compile` gains a flag that limits a build to auto-build folders, and the scheduled job uses it. The Watched folders list moves out from under the automation toggle.

One cost to point out in the UI: watched folders hold originals, not copies. Editing a file changes its hash, so it is ingested and staged again, and an auto-build folder pays to rebuild it. The batching window and the spend cap limit this.

Ingesting on arrival needs something running while the app is closed, which is the background agent (section 7). Until then, scheduled runs can keep ingesting watched folders, and the per-folder switch still decides what they build.

## 7. Architecture: a background agent



### 7.1 The questions

Is it worth replacing the shell-outs with a real app process? Can ingestion run without the menu bar app open?

Today, automatic ingestion depends on the app. `DropWatcher` (FSEvents on `drops/`) and `AutoRunner` only run while the app is open, and the launchd schedule is the only backstop. `ingest --watch` exists (`cli.py:164-172`), but in watch mode it doesn't run triage or refresh `.state.json` (`ingestion/watcher.py:114-136`), so it can't replace the app's watcher yet.

### 7.2 Recommendation

Build a background agent. Don't bundle Python into the `.app` yet.

The problems in section 6 exist because no single process owns the pipeline's state. A long-running process that owns it fixes the missing contract, the lock, and persistence at the same time. Packaging is a separate problem, and for now it costs much more than it returns.

### 7.3 The agent

A new command, `second-brain agent`, is installed as a LaunchAgent (`com.secondbrain.agent`) with `RunAtLoad` and `KeepAlive`, so it starts at login and restarts after a crash. It owns:

- Watching `drops/` and the watched folders. This reuses `watch_sources`, extended so each batch runs triage, refreshes state, and writes the run record.
- Automatic builds for folders that allow them (6.5), at the scheduled times.
- A job queue that runs one writing job at a time: ingest, triage, cluster preview, build, recompile, forget, triage decisions, domain edits, and wiki merge. Running them one at a time replaces the lock file for everything that goes through the agent.
- The build schedule, replacing the calendar-interval plist.
- Status and run outcomes, served to clients instead of written to dotfiles.

Heavy parsing (Docling, Chandra) and builds run in worker subprocesses that the agent starts and waits on. That keeps the resident process small and hands model memory back to the system after each batch, the same memory profile as today's one process per run.

### 7.4 The API

The agent serves HTTP and JSON on `127.0.0.1` on a random port. It writes the port and a random bearer token to `~/second-brain/.agent.json` with mode 0600, and every request has to carry the token. Loopback TCP beats a Unix socket here because `URLSession` supports it directly, while a Unix socket would need a hand-written HTTP client on `Network.framework`. `uvicorn`, `starlette`, and `sse-starlette` are already in `uv.lock` through `mcp[cli]`, so this adds no dependencies.

Initial endpoints:

- `GET /version` returns the protocol version. The app refuses to run against a different major version and says why.
- `GET /catalog` returns the data from 6.2.
- `GET /state` returns staged sources, costs, counts, the queue, the current job, and the last run outcome.
- `GET /events` streams server-sent events for status ticks, queue changes, and run outcomes. This replaces the app's one-second polling of dotfiles and `manifest.db`.
- `POST /jobs` takes `{kind, args}`, and `POST /jobs/current/stop` asks the running job to stop.
- `GET /settings` and `PATCH /settings`, validated by the pydantic config model.
- Resource endpoints for everything the app does today: sources (forget, retry, skip, keep), domains (list, rename, merge, delete), health, and wiki merge and dismiss.

The CLI still runs in-process for scripting. Commands that write check for a running agent and submit a job to it. If no agent is running, they take the Python lock from 6.4 and run directly.

### 7.5 Packaging

Embedding Python in the `.app` isn't worth it yet. Docling, `chandra-ocr`, and `mlx-vlm` pull in PyTorch and Transformers, which puts the environment in the gigabytes. Notarizing that many native libraries is slow and fragile, and Ollama is a separate install either way.

What would make this feel like a real app is cutting its ties to the repo checkout:

- Install the pipeline with `uv tool install` into a stable location, and have the installer record that executable in place of `run.sh`.
- Keep user config in the vault (6.3).
- On first launch, if the agent isn't installed, the app offers to run the installer, instead of each tab saying "Reinstall".



### 7.6 Interim option

If the agent is a long way off, a second LaunchAgent with `WatchPaths` on `drops/documents` and `drops/chatgpt` that runs `run.sh drops` would ingest drops without the app, in about thirty lines of `scheduler/launchd.py`. `WatchPaths` isn't recursive, so it wouldn't cover nested watched folders. It becomes throwaway code once the agent exists.

### 7.7 Migration

1. Sections 1 through 6, in the current structure. The agent reuses the build loop, the run record, and the catalog, so none of that work is wasted.
2. The agent process: watching, the queue, the schedule, and worker subprocesses. The app keeps reading files for now, and `DropWatcher` and `AutoRunner` are removed from it.
3. The API. Move the app's reads over first (manifest queries, state, build log, health, domains), then its writes (settings, source actions, domain actions).
4. The packaging changes from 7.5.

Risks and mitigations:

- A crash loop under `KeepAlive`: launchd throttles restarts, and the run record should capture the crash reason.
- Protocol drift between the app and the agent: handled by `/version`.
- Two writers during the migration: handled by the lock from 6.4.



## 8. Compilation providers and outside agents



### 8.1 Where the coupling is

The build loop speaks Anthropic's Messages format directly: content blocks, `tool_use` and `tool_result`, `stop_reason == "end_turn"`, Anthropic's usage field names, and `cache_control` (`compiler.py:491-645`). `compact_history` walks the same message shape (`agent.py:987-1070`), the tool schemas use `input_schema` (`agent.py:209`), and the error handling names `anthropic.BadRequestError` (`compiler.py:373`).

That matters less than it looks. Other services now accept this format. DeepSeek serves it, which is why DeepSeek support is only a different base URL (`llm_providers.py:68-74`). OpenRouter serves it at `/api/v1/messages` for any model it routes, translating tool calls for non-Claude models.

The coupling that does cost something is the closed model table (`llm_providers.py:22-47`). `resolve_profile` rejects any model that isn't listed, and every price, context window, and cache multiplier is typed in by hand and copied into Swift (6.2). Adding a model means a code change in two languages.

The loop itself should stay. The tools, the path sandbox, `write_page` validation, provenance stamping, the spend check after each turn, the stop flag, and commit or rollback per source are what make the output trustworthy. A model router doesn't replace any of them.

### 8.2 Proposed: OpenRouter as a provider, with an open model list

- Add `openrouter` to `_PROVIDERS` with `base_url="https://openrouter.ai/api"` and `OPENROUTER_API_KEY`. The Anthropic SDK and the loop stay as they are. OpenRouter passes `cache_control` through to Claude models, including the 1-hour TTL the loop uses.
- For OpenRouter, load the model list (id, prices, context length) from its models API instead of the hand-written table, and cache it in the vault. Any listed model becomes selectable, and the app gets the list through the catalog from 6.2.
- Prefer the cost OpenRouter reports on each response over the local estimate, falling back to the estimate when the field is missing. OpenRouter documents `usage.cost` for its OpenAI-style endpoint; check that `/api/v1/messages` returns it too before depending on it.
- Handle OpenRouter's 402s separately (1.1). It returns 402 for an empty balance and also for a temporary in-flight spending limit (`metadata.reason: in_flight_budget_exhausted`, with a `Retry-After` header). Only the first should abort the build.
- Keep the direct Anthropic and DeepSeek providers, which avoid a middleman.

What routing through OpenRouter costs:

- Another company sees the source text. That cuts against the local-first description in `architecture.md`, so Settings should say so when OpenRouter is selected.
- Billing runs through OpenRouter credits instead of the provider account.
- The same model id can be served by different upstream providers unless routing is pinned, so behavior and caching can vary between runs.



### 8.3 Swappable models are not swappable agents

OpenRouter makes the model swappable. It doesn't run agent loops, so it doesn't let Claude Code, Codex, or a Cursor agent do the compiling. That would need a different seam: the compile tools themselves (8.4).

Not every model can drive this loop, either. A build is a long run of structured tool calls, and a weak tool-caller will run out of turns or write malformed pages. Before opening the model list in the app, add a small evaluation: compile a fixed set of sources with each candidate model, and record the run outcome, the cost, and the health report of the result (broken links, missing frontmatter, new gaps). Models that pass get marked as recommended.

### 8.4 Outside agents, if wanted later

Expose the compile tools (`write_page`, `set_page_meta`, a frontmatter-safe `edit_file`, and the read and search tools) as an MCP server started for one source group at a time. Scoping it to one group lets it track changes and stamp provenance the way `WikiToolExecutor` does now. The general MCP server stays as it is, read-only plus `capture_note`, which drops notes into the ingest queue rather than writing pages (`mcp_server/tools.py:455-506`).

The compiler then chooses between two backends: the built-in loop, or an external agent run headless with that MCP server and nothing else. Validation, commit, and rollback stay in the compiler around either one.

An external backend gives up:

- The spend check after each turn. Live cost becomes whatever the external agent reports.
- A clean stop between turns. The process gets killed instead.
- Control over prompt caching.
- The guarantee that pages are only written through validated tools, unless the agent's own file and shell tools are turned off.

Recommendation: do 8.2 now, since it's a small change with a direct payoff. Hold 8.4 until there is a specific agent worth supporting, and build it after the background agent (section 7), since both change how builds run.

### 8.5 Keeping the loop independent of any one provider

What changes when a lab ships an update, and where it lands today:

- Model names, prices, and context sizes change every few months. Today they are code (`llm_providers.py:22-47`, plus the Swift copy).
- Request formats and features change: caching options, reasoning settings, new tool types, error codes (the 402 billing error is recent). Today the loop calls the Anthropic SDK directly.
- The part of the API the loop actually uses is small and stable across providers: a system prompt, tool definitions as JSON Schema, a conversation of tool calls and tool results, a stop reason, and token counts.

The standard fix is an adapter layer. The loop works with its own neutral types (a turn with text, tool calls, a stop reason, and usage), and one adapter per request format converts to and from the provider. Tool definitions are stored as plain JSON Schema and converted in the adapter, and `compact_history` works on the neutral types. Caching becomes a hint ("this prefix is stable") that the Anthropic adapter turns into `cache_control` and other adapters map to their own mechanism or ignore. Two formats reach almost every provider through compatibility endpoints: Anthropic Messages (Anthropic, DeepSeek, OpenRouter) and OpenAI Chat Completions (OpenAI, OpenRouter, Gemini, DeepSeek, and local servers such as Ollama).

Three ways to get the adapters:

1. Write them. The neutral types and two adapters come to a few hundred lines with no new dependencies, and caching and cost stay fully under our control. We maintain them, but the subset they cover rarely changes.
2. Use Pydantic AI for the model layer only. Its direct API (`pydantic_ai.direct.model_request`) sends one request with JSON Schema tool definitions to any supported provider (`anthropic:`, `openai:`, `google:`, and OpenAI-compatible endpoints) and returns neutral parts and usage. The loop, tools, spend check, stop flag, and rollback stay ours, and the Pydantic team tracks provider changes. Before committing, confirm that Anthropic cache settings and per-request cost work through the direct API (both are documented for full agent runs, including a 1-hour TTL on instructions and tool definitions), and look at how often its own releases break compatibility (version 2.0 is out).
3. Use Pydantic AI's full agent. `agent.iter()` steps through a run one model request at a time, so the spend check and stop flag fit between requests. `ProcessHistory` covers history compaction, and `UsageLimits` caps requests. This hands over the most maintenance, but it rewrites the most delicate code and moves the dependence from one provider's SDK to one framework.

Not recommended: LiteLLM. It covers the same ground, but its PyPI releases 1.82.7 and 1.82.8 were backdoored in March 2026 and stole credentials from the machines that installed them. This app keeps provider keys in `.env` on the user's Mac, so a large gateway dependency is a poor trade.

With any option, two things apply. Model names, prices, and context sizes become data (a catalog file, or a provider's model list) instead of code, and an unknown model is allowed with its cost marked as an estimate. And the evaluation from 8.3 runs before the default model changes, which is the real protection against an update quietly making pages worse.

Recommendation: start by moving the catalog to data and putting the single `messages.create` call behind an interface, which pays off on its own. Then take option 2 if its caching and cost checks pass, and option 1 if they don't.

## 9. UI text

Many help strings are long, explain internals the user doesn't need, or use different words for the same thing. Guidelines, following the humanizer skill:

- Say what the control does and what happens when you use it, in one or two short sentences.
- Address the user as "you" and use active voice.
- Leave out where things are stored and how they run (`.env`, `config.yaml`, launchd). Mention an internal only when the user needs it to act.
- Use one name per concept: "build" for compiling the wiki, "ingest" for converting files, "source" for an ingested file, "keep" and "skip" for triage decisions.
- Don't use em dashes. Use a period, a comma, or a colon instead.
- Don't mention settings that don't exist.

Problems found, all fixed in the working tree (9.1):

- `SettingsView.swift:248` refers to a "Handwriting setting" the app no longer has.
- The "API key required" alert says building "uses Claude" even when DeepSeek is selected (`ContentView.swift:55-56`).
- The "Run automatically" help doesn't say that scheduled runs build and cost money.
- The Chats tab labels a kept chat "worthwhile", but the button that sets it says "Keep".
- Several tooltips use em dashes ("Skip — set aside", "Keep — restore to the build", "Retry — requeue for the next build").
- The Overview explains every check twice (4.1). The wording is fixed; the duplication remains until 4.1.



### 9.1 Status

Applied in the working tree, not yet committed: help text in Settings, the alerts, section help and tooltips in every tab, empty states, the Overview explanations, and the file-picker prompt. The Settings group "Wiki compilation" is renamed "Build", the MCP group has a short explanation, and a kept chat's badge reads "kept" instead of "worthwhile". The `git diff` holds the before and after.

Still open:

- The Automation text describes today's behavior. If 6.5 lands, it becomes:
  - Watched folders: "Folders Second Brain watches for new files. Anything added here is ingested automatically, just like files you drop into the app."
  - Build automatically (a switch on each folder row): "Add this folder's new files to the wiki at the scheduled times. Automatic builds cost money and stop at your spend cap."
  - Schedule: "When automatic builds run. If your Mac is asleep, the build starts when it wakes."
- The Ingest "Failed" help can add "Hover a row to see why" once 1.5 stores failure reasons.
- The Overview explanations stay in Swift until 4.1 moves them into `health --json`. Wording for that move:
  - Referenced but not written: "Topics your pages link to that don't have a page yet, most linked first."
  - No backlinks: "Pages that no other page links to. Search still finds them. New pages start here until something links to them."
  - Possible duplicates: "Pairs of pages that cover very similar material."
  - Oversized pages: "Pages over {SPLIT_THRESHOLD} words. Consider splitting them."
  - Short pages: "Pages under {MERGE_THRESHOLD} words."
  - Missing frontmatter: "Pages missing a title, a type, or at least one domain."
- The labels are decided (section 10, decided item 6).



## 10. Decisions

Decided:

1. Build log: one entry per page, written once a source commits, tagged with its source (section 2).
2. Errors not caused by the source stop the build, including rate limits and network failures that outlast the SDK's retries (1.1).
3. Cleanup of `raw/.skipped/` and the reviewed plan happens only after a fully successful build (1.1).
4. A build stopped by the spend cap shows an amber line with the number of sources left (1.3).
5. Exit codes: 0 for ok or stopped, 1 for failed, 2 for partial or capped (1.8).
6. Labels stay as they are: "Not linked from any page", "Over 4,000 words", "Under 150 words", "Missing a title, type, or domain".
7. The run record keeps one entry per stage, so a successful ingest never hides a failed build (1.3).

Open:

1. Whether automatic building becomes a per-folder switch (6.5) or stays one global toggle.
2. Agent transport: loopback TCP with a token (proposed) or a Unix socket.
3. Whether to ship the interim `WatchPaths` agent (7.6) or wait for the full agent.
4. Model layer: Pydantic AI's direct API or our own adapters, decided by the caching and cost checks in 8.5. Error kinds are defined at the model-call interface, so this choice doesn't change them.
5. Whether sending sources through OpenRouter is acceptable for this project (8.2), and whether any outside agent is worth supporting (8.4).



## 11. Direction and order

The direction behind all of this: one process owns the pipeline's state, and the app and the CLI are its clients. Ingesting is free and automatic. Building costs money and happens only when the user, or a per-folder rule the user set, asks for it. Every run reports how it ended. The build loop doesn't depend on any one provider.

Each phase below is useful on its own, and later phases reuse earlier work instead of replacing it.

Phase 0, UI copy and cleanup. Done; see the implementation status table.

Phase 1, visible failures and honest records. These change only the pipeline, plus the app's footer, so any later architecture keeps them. One commit each, in this order:

1. Put the single model call behind an interface that raises one error type carrying a kind (account, rate limit, network, source) and a plain reason. Today it wraps the Anthropic SDK; Pydantic AI or our own adapters can replace it later without touching the build loop (8.5).
2. Stop the build on non-source errors, and clean up only after full success (1.1).
3. Keep the cost already spent when a source fails partway through (1.4).
4. Write the run outcome record from ingest and build (1.3), and use it for exit codes and `run.sh` (1.8).
5. Show the footer line in the app (1.3).
6. Report ingest with Ollama down as a failed run (1.7), store ingest failure reasons (1.5), list failed watched-folder files (1.6), and fix the docs (1.9).
7. The current source in the heartbeat (3).
8. The Recent list written once per source, after commit (2).
9. The frontmatter guards (4.2), then the gap names in the prompt and the guard in `write_page` (5).

Phase 2, one source of truth for settings and constants.

- `catalog --json` (6.2), user config in the vault with validated writes (6.3), and the Python lock (6.4).
- The model catalog as data (the rest of the first step of 8.5).

Phase 3, automation reshaped (6.5): ingesting separated from building, with a per-folder "Build automatically" switch. This works on the current launchd schedule.

Phase 4, the background agent (7): ingest on arrival, the job queue, and the API, with the app's reads and then its writes moved onto it. `DropWatcher`, `AutoRunner`, the SQL in `ManifestReader`, and the YAML editor in `ConfigStore` go away here.

Phase 5, providers: the adapter layer (8.5), then OpenRouter (8.2), with the model evaluation (8.3). This doesn't depend on phases 3 and 4, so it can move earlier if model choice becomes pressing.

Later, only if wanted: packaging (7.5), outside agents (8.4), and "Point links here" (5.3).

Skipped on purpose: the interim `WatchPaths` job (7.6), and a separate fix for the half-finished removes in `ManifestMutator` (6.1). The phase 4 API replaces both.
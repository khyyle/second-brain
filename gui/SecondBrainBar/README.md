# Second Brain Bar

A macOS menu bar client for the Second Brain backend. It submits imports and build requests over HTTP and receives library and job updates through server-sent events (SSE).

The top-level [`install.sh`](../../install.sh) builds the menu bar app from this checkout and installs it in Applications.

For development, see [Run locally](../../CONTRIBUTING.md#run-locally).

## Source layout

Files are located under `Sources/SecondBrainBar/`, organized by feature and role:

| Directory | Responsibility |
| --- | --- |
| `App/` | Application lifecycle, menu bar item, and root window presentation. |
| `API/` | HTTP client, event stream, and shared response types. |
| `Features/` | Primary user-facing screens and their private views and readers, organized by domain. |
| `Intake/` | File and folder selection, drag-and-drop handling, and import submission. |
| `Pipeline/` | Shared library state, job progress, and triage results received from the API. |
| `Configuration/` | Startup paths and preferences response types. |
| `Shared/` | Cross-cutting visual theme, custom controls, view modifiers, and utilities. |

Directory conventions:

- Each file is named after its primary type. Small helpers used only by that type stay private in the same file. The exceptions are files grouping related data models (such as `ClusterPlan.swift`) and free utility functions (such as `Formatting.swift`).
- Feature code stays inside its feature directory. If another feature needs the same code, move it into `Shared/`, `Pipeline/`, `API/`, or `Configuration/`.
- Shared pipeline models live in `Pipeline/`, even when only one screen renders them. Models used only within a feature stay alongside its views.
- Dependencies point inward. `App/` and `Features/` use `API/`, `Pipeline/`, `Configuration/`, `Intake/`, and `Shared/`; those folders must not depend on types defined in `App/` or `Features/`.
- Views and their data models send requests through `BackendClient`; `PipelineStore` holds the latest library state and job progress shared across screens.
- Name extensions on system types using the `Type+Feature.swift` convention, such as `View+OnPanelShow.swift`.

## Backend connection

- **Vault:** `SECOND_BRAIN_VAULT` from the process environment, defaulting to `~/second-brain`.
- **Address:** `http://127.0.0.1:<port>/v1`, with `SECOND_BRAIN_PORT` defaulting to `8000`.
- **Authentication:** the bearer token stored in the selected vault's `.backend/api-token` file.
- **Backend:** the app reuses a responding backend or starts one from `~/Library/Application Support/Second Brain/backend`. It stops only its own backend on quit. In development, `make dev` starts the checkout’s backend before launching the GUI.

## Dependencies

The Swift app uses `URLSession` to talk to the local backend, which manages provider connections, databases, and vault files.

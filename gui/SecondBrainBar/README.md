# Second Brain Bar

A macOS menu bar client for the Second Brain backend. It submits imports and build requests over HTTP and receives library and job updates through server-sent events (SSE).

Most users install it via the repository's top-level `install.sh`, which builds this app and copies it into `/Applications`. The instructions below are for building it on its own.

## Build

```sh
cd gui/SecondBrainBar
./bundle.sh
cp -r "Second Brain.app" /Applications/
```

`bundle.sh` compiles a release binary, generates `AppIcon.icns` from `Resources/AppIcon.png`, and ad-hoc signs the bundle. Replace `Resources/AppIcon.png` (1024x1024) and re-run to change the icon.

For development, run from the repository root:

```sh
make dev
```

This builds and launches a debug app with the local backend. See [development](../../docs/development.md#start-it) for further details and startup options.

## Source layout

Files are located under `Sources/SecondBrainBar/`, organized by feature and role:

| Directory | Responsibility |
| --- | --- |
| `App/` | Application lifecycle, menu bar item, and root window presentation. |
| `API/` | HTTP client, event stream, and shared response types. |
| `Features/` | Primary user-facing screens and their private views and readers, organized by domain. |
| `Intake/` | File and folder selection, drag-and-drop handling, and import submission. |
| `Pipeline/` | Shared library state, job progress, and triage results received from the API. |
| `Configuration/` | Startup paths, preferences response types, and provider Keychain access. |
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
- **Launcher:** `SECOND_BRAIN_BACKEND_SCRIPT`, or the path to `run.sh` recorded by `bundle.sh` in `Contents/Resources/BackendLauncher.txt`. The app first tries to connect to an existing backend. If none is available, it starts one using that launcher and stops it when the app quits.

## Dependencies

The Swift target uses the macOS SDK, including `URLSession` for HTTP and Security.framework for Keychain access. The Python backend manages the databases and changes to vault files.

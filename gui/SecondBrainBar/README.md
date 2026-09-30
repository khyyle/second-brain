# Second Brain Bar

A macOS menu bar companion to the Second Brain pipeline. It stages dropped files into `~/second-brain/drops/`, reads the pipeline manifest (read-only) to report ingest status, and can trigger a pipeline run.

Most users install it via the repository's top-level `install.sh`, which builds this app and copies it into `/Applications`. The instructions below are for building it on its own.

## Build

```sh
cd gui/SecondBrainBar
./bundle.sh
cp -r "Second Brain.app" /Applications/
```

`bundle.sh` compiles a release binary, generates `AppIcon.icns` from `Resources/AppIcon.png`, and ad-hoc signs the bundle. Replace `Resources/AppIcon.png` (1024x1024) and re-run to change the icon.

For development you can run it attached to the terminal instead:

```sh
swift run
```

## Source layout

Files are located under `Sources/SecondBrainBar/`, organized by feature and role:

| Directory | Responsibility |
| --- | --- |
| `App/` | Application lifecycle, menu bar item, and root window presentation. |
| `Features/` | Primary user-facing screens and their private views and readers, organized by domain. |
| `Intake/` | File capture, drag-and-drop handling, system open dialogs, and folder monitoring. |
| `Pipeline/` | Backend communication: running the Python process, reading and updating its records, and tracking live status. |
| `Configuration/` | User settings, local environment storage, vault path resolution, and model profiles. |
| `Shared/` | Cross-cutting visual theme, custom controls, view modifiers, and utilities. |

Directory conventions:

- Each file is named after its primary type. Small helpers used only by that type stay private in the same file. The exceptions are files grouping related data models (such as `ClusterPlan.swift`) and free utility functions (such as `Formatting.swift`).
- Feature code stays inside its feature directory. If another feature needs the same code, move it into `Shared/`, `Pipeline/`, or `Configuration/`.
- Pipeline output models stay in `Pipeline/` even if only a single screen renders them. This prevents pipeline readers from depending on UI feature code.
- Dependencies point inward. `App/` and `Features/` call into foundational layers (`Pipeline/`, `Configuration/`, `Intake/`, `Shared/`), but foundational layers never import or reference types from `Features/` or `App/`.
- Name extensions on system types using the `Type+Feature.swift` convention, such as `View+OnPanelShow.swift`.

## How it locates the pipeline

- **Vault:** `~/second-brain`, matching the `data_dir` default in `config/config.yaml`.
- **Pipeline script:** read from `~/second-brain/.pipeline-script`, a file containing the absolute path to `run.sh` that `install.sh` writes. If it is missing, the run action is disabled. No paths are hardcoded.

## Dependencies

None outside the macOS SDK. `import SQLite3` reads the manifest directly via the system library (linked in `Package.swift`).

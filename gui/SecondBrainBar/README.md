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

## How it locates the pipeline

- **Vault:** `~/second-brain`, matching the `data_dir` default in `config/config.yaml`.
- **Pipeline script:** read from `~/second-brain/.pipeline-script`, a file containing the absolute path to `run.sh` that `install.sh` writes. If it is missing, the run action is disabled. No paths are hardcoded.

## Dependencies

None outside the macOS SDK. `import SQLite3` reads the manifest directly via the system library (linked in `Package.swift`).

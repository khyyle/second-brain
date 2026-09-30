import Foundation
import AppKit

/// User-initiated manifest mutations from the GUI.
///
/// The relational cascade lives in the Python CLI (one source of truth for
/// the manifest schema); the GUI does the macOS-native file op (move to
/// Trash) and shells out to the CLI for the records. The lists refresh on
/// their poll timers; for file-backed deletes the row disappears as soon as
/// the file is trashed, regardless of when the DB op lands.
enum ManifestMutator {
    /// Un-ingest a staged source: trash its raw markdown and clear its
    /// manifest / compiled / triage records.
    static func removeStagedSource(config: AppConfig, rawRel: String) {
        trash(config.rawRoot.appending(path: rawRel))
        run(config, "forget \(quote(rawRel))")
    }

    /// Remove a queued or failed dropped file: trash it and drop its row.
    static func removeSource(config: AppConfig, filePath: String) {
        trash(URL(fileURLWithPath: filePath))
        run(config, "forget-drop \(quote(filePath))")
    }

    /// Keep a source (the Keep action): record the verdict; the file stays
    /// in raw/ and becomes staged for build.
    static func setTriageDecision(config: AppConfig, rawPath: String, decision: String) {
        run(config, "triage set \(quote(rawPath)) \(decision)")
    }

    /// Skip a source: move its raw markdown into the hidden `raw/.skipped/`
    /// holding folder (out of the build, but recoverable) and record the
    /// skip verdict. A completed build later purges the folder.
    static func skipSource(config: AppConfig, rawRel: String) {
        move(config.rawRoot.appending(path: rawRel), to: skippedURL(config, rawRel))
        run(config, "triage set \(quote(rawRel)) skip")
    }

    /// Un-skip: move the source back out of `raw/.skipped/` and record it
    /// worthwhile again, so it re-stages. A no-op on the file if a build has
    /// already purged it (the verdict still flips).
    static func unskipSource(config: AppConfig, rawRel: String) {
        move(skippedURL(config, rawRel), to: config.rawRoot.appending(path: rawRel))
        run(config, "triage set \(quote(rawRel)) worthwhile")
    }

    /// Location of a source inside the skipped holding folder, preserving its
    /// lane subpath (e.g. `raw/.skipped/chatgpt/foo.md`).
    static func skippedURL(_ config: AppConfig, _ rawRel: String) -> URL {
        config.rawRoot.appending(path: ".skipped").appending(path: rawRel)
    }

    /// Re-ingest a failed dropped file (the Retry action). Re-runs the local
    /// parser; a failed batch cached nothing, so this re-OCRs cleanly. No
    /// API key needed.
    static func retryIngest(config: AppConfig, filePath: String) {
        run(config, "ingest --path \(quote(filePath))")
    }

    private static func trash(_ url: URL) {
        if FileManager.default.fileExists(atPath: url.path) {
            try? FileManager.default.trashItem(at: url, resultingItemURL: nil)
        }
    }

    /// Move a file, creating the destination's parent and overwriting any
    /// existing file there. A no-op when the source is missing.
    private static func move(_ source: URL, to destination: URL) {
        let fileManager = FileManager.default
        guard fileManager.fileExists(atPath: source.path) else { return }
        try? fileManager.createDirectory(
            at: destination.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        if fileManager.fileExists(atPath: destination.path) {
            try? fileManager.removeItem(at: destination)
        }
        try? fileManager.moveItem(at: source, to: destination)
    }

    private static func run(_ config: AppConfig, _ command: String) {
        guard let repo = config.repoDir else { return }
        PipelineRunner.runManaged(repoDir: repo, command: command)
    }

    /// POSIX single-quote a CLI argument so paths with spaces survive the
    /// shell-out (and embedded single quotes are escaped).
    private static func quote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}

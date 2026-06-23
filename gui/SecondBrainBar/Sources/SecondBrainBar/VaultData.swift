import Foundation

/// A staged file that has not finished ingestion yet.
struct QueueItem: Identifiable, Hashable {
    let id: String          // absolute path
    let displayName: String
    let state: State
    let since: Date         // when it entered its current state (for elapsed)

    enum State {
        case waiting        // in drops, not yet seen by the pipeline
        case processing     // currently being ingested
        case failed         // ingestion failed

        var label: String {
            switch self {
            case .waiting:    return "waiting"
            case .processing: return "processing"
            case .failed:     return "failed"
            }
        }
    }
}

/// A raw source that has been ingested and passed triage but is not yet
/// compiled into the wiki.
struct StagedSource: Identifiable, Hashable {
    let id: String          // path relative to raw/
    let displayName: String
    let bytes: Int64

    var sizeText: String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

/// Aggregate pipeline metrics for the status summary.
struct VaultStats {
    let staged: Int       // ingested sources ready to build
    let wikiPages: Int    // pages compiled into the wiki
    let byteSize: Int64   // size of the raw output

    static let empty = VaultStats(staged: 0, wikiPages: 0, byteSize: 0)

    var sizeText: String {
        ByteCountFormatter.string(fromByteCount: byteSize, countStyle: .file)
    }
}

/// Reads queue and stats from the filesystem + manifest. Kept off the
/// SwiftUI types so views stay declarative.
enum VaultData {
    private static let supported: Set<String> = ["pdf", "md", "txt", "tex", "json"]

    /// Files staged in `drops/` that are not yet completed.
    static func queue(config: AppConfig) -> [QueueItem] {
        let statuses = ManifestReader(dbPath: config.manifestDB).statusByPath()
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(
            at: config.dropsRoot,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { return [] }

        var items: [(QueueItem, Date)] = []
        while let url = enumerator.nextObject() as? URL {
            guard supported.contains(url.pathExtension.lowercased()) else { continue }
            let info = statuses[url.path]
            if info?.status == .complete { continue }

            let state: QueueItem.State
            switch info?.status {
            case .processing: state = .processing
            case .failed:     state = .failed
            default:          state = .waiting
            }
            let mod = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? Date.distantPast
            // For a processing item, "since" is when it entered processing.
            let since = info?.updatedAt ?? mod
            items.append((
                QueueItem(id: url.path, displayName: url.lastPathComponent, state: state, since: since),
                mod
            ))
        }
        return items.sorted { $0.1 > $1.1 }.map(\.0)
    }

    /// An uncompiled raw markdown source with its triage decision, before any
    /// staged/deferred classification.
    private struct RawSource {
        let staged: StagedSource
        let decision: String?
        let modified: Date
    }

    /// Every uncompiled raw markdown source, paired with its triage decision.
    private static func uncompiledRawSources(config: AppConfig) -> [RawSource] {
        let reader = ManifestReader(dbPath: config.manifestDB)
        let compiled = reader.compiledRawPaths()
        let triage = reader.triageDecisionMap()
        let fm = FileManager.default
        let base = config.rawRoot.path + "/"
        guard let enumerator = fm.enumerator(
            at: config.rawRoot,
            includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var out: [RawSource] = []
        while let url = enumerator.nextObject() as? URL {
            guard url.pathExtension.lowercased() == "md" else { continue }
            let rel = url.path.replacingOccurrences(of: base, with: "")
            if compiled.contains(rel) { continue }
            let values = try? url.resourceValues(
                forKeys: [.fileSizeKey, .contentModificationDateKey]
            )
            out.append(
                RawSource(
                    staged: StagedSource(
                        id: rel,
                        displayName: url.deletingPathExtension().lastPathComponent,
                        bytes: Int64(values?.fileSize ?? 0)
                    ),
                    decision: triage[rel],
                    modified: values?.contentModificationDate ?? Date.distantPast
                )
            )
        }
        return out
    }

    /// Sources ready to compile: uncompiled raw markdown that triage did not
    /// skip or hold for review, and that the build did not defer as too large.
    /// Must match the compiler's own source selection, or the staged count
    /// would mislead.
    static func stagedSources(config: AppConfig) -> [StagedSource] {
        // Most-recently ingested first, so the capped view shows newest.
        return uncompiledRawSources(config: config)
            .filter { $0.decision != "skip" && $0.decision != "review" && $0.decision != "deferred" }
            .sorted { $0.modified > $1.modified }
            .map(\.staged)
    }

    /// Sources the build deferred because they exceed the current model's
    /// context window. Held out of the staged set until a larger-window model
    /// is selected, when the next build re-admits them automatically.
    static func deferredSources(config: AppConfig) -> [StagedSource] {
        return uncompiledRawSources(config: config)
            .filter { $0.decision == "deferred" }
            .sorted { $0.modified > $1.modified }
            .map(\.staged)
    }

    /// Rough USD estimate for compiling the staged set. Approximate: the
    /// agent re-reads each source over a few turns and writes ~2 pages.
    /// Calibrated to observed ~$0.07-0.20 per source; live cost during the
    /// build is authoritative.
    static func estimatedBuildCost(_ staged: [StagedSource], model: String) -> Double {
        let price = LLMProvider.modelPrice(model)
        var usd = 0.0
        for s in staged {
            let tokens = max(Double(s.bytes) / 4.0, 500)
            usd += tokens * 4 * price.input / 1_000_000     // ~4 turns of context
            usd += 4000 * price.output / 1_000_000          // ~2 generated pages
        }
        return usd
    }

    /// Aggregate pipeline metrics (staged, built, raw size).
    static func stats(config: AppConfig) -> VaultStats {
        return VaultStats(
            staged: stagedSources(config: config).count,
            wikiPages: wikiPageCount(config: config),
            byteSize: directorySize(config.rawRoot)
        )
    }

    /// Count of compiled wiki content pages (excludes generated views/meta).
    private static func wikiPageCount(config: AppConfig) -> Int {
        let fm = FileManager.default
        let contentDirs = ["concepts", "problems", "projects", "insights", "syntheses"]
        var count = 0
        for dir in contentDirs {
            let url = config.wikiRoot.appending(path: dir)
            guard let items = fm.enumerator(
                at: url,
                includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            ) else { continue }
            while let f = items.nextObject() as? URL {
                if f.pathExtension.lowercased() == "md" { count += 1 }
            }
        }
        return count
    }

    private static func directorySize(_ root: URL) -> Int64 {
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(
            at: root,
            includingPropertiesForKeys: [.totalFileAllocatedSizeKey, .fileSizeKey],
            options: [.skipsHiddenFiles]
        ) else { return 0 }

        var total: Int64 = 0
        while let url = enumerator.nextObject() as? URL {
            let values = try? url.resourceValues(forKeys: [.totalFileAllocatedSizeKey, .fileSizeKey])
            total += Int64(values?.totalFileAllocatedSize ?? values?.fileSize ?? 0)
        }
        return total
    }
}

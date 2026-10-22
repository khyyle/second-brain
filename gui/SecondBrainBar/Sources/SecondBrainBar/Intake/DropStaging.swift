import CryptoKit
import Foundation

/// Copies dropped or imported files into the pipeline's drop folders.
///
/// Every file is copied; the pipeline decides what is new. Copies whose exact
/// content is already in the vault are reported, so the ingest list can say so
/// even though the pipeline clears them almost at once. Numbered names preserve
/// files whose names would collide with another queued or parsed source.
enum DropStaging {
    static let documentExtensions: Set<String> = ["pdf", "md", "txt", "tex"]

    struct Result {
        var failed = false
        var files: [QueueItem] = []

        var added: Int { files.count }
    }

    /// Copy supported documents (expanding folders) into the documents lane.
    static func stageDocuments(_ selection: [URL], config: AppConfig) -> Result {
        stage(documentFiles(in: selection), intoLane: "documents", config: config)
    }

    /// Copy a provider's export files into its lane.
    static func importExport(_ provider: ExportProvider, files: [URL], config: AppConfig) -> Result {
        stage(files, intoLane: provider.lane, config: config)
    }

    static func isAlreadyIngested(_ files: [URL], config: AppConfig) -> Bool {
        let ingested = ManifestReader(dbPath: config.manifestDB)
            .ingestedContentHashes(rawRoot: config.rawRoot)
        return !files.isEmpty && files.allSatisfy { file in
            contentHash(of: file).map { ingested.contains($0) } ?? false
        }
    }

    private static func stage(_ files: [URL], intoLane lane: String, config: AppConfig) -> Result {
        let ingested = ManifestReader(dbPath: config.manifestDB)
            .ingestedContentHashes(rawRoot: config.rawRoot)
        var result = Result()
        for file in files {
            let submittedAt = Date()
            let alreadyIngested = contentHash(of: file).map { ingested.contains($0) } ?? false
            guard let target = copy(file, intoLane: lane, config: config) else {
                result.failed = true
                continue
            }
            result.files.removeAll { $0.id == target.path }
            result.files.append(QueueItem(
                id: target.path,
                displayName: target.lastPathComponent,
                state: alreadyIngested ? .duplicate : .waiting,
                since: submittedAt
            ))
        }
        return result
    }

    /// SHA-256 of a file's bytes, read in chunks so a large PDF isn't loaded at once.
    private static func contentHash(of file: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try? handle.read(upToCount: 1 << 16), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func documentFiles(in selection: [URL]) -> [URL] {
        let fileManager = FileManager.default
        var files: [URL] = []
        for url in selection {
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory) else { continue }
            if isDirectory.boolValue {
                let enumerator = fileManager.enumerator(
                    at: url,
                    includingPropertiesForKeys: [.isRegularFileKey],
                    options: [.skipsHiddenFiles, .skipsPackageDescendants]
                )
                while let child = enumerator?.nextObject() as? URL {
                    if documentExtensions.contains(child.pathExtension.lowercased()) {
                        files.append(child)
                    }
                }
            } else if documentExtensions.contains(url.pathExtension.lowercased()) {
                files.append(url)
            }
        }
        return files
    }

    /// Copy a file into a lane, returning where it landed, or nil if it couldn't.
    private static func copy(_ source: URL, intoLane lane: String, config: AppConfig) -> URL? {
        let fileManager = FileManager.default
        let destination = config.dropsRoot.appending(path: lane)
        do {
            try fileManager.createDirectory(at: destination, withIntermediateDirectories: true)
            let directories = [
                destination,
                config.rawRoot.appending(path: lane),
                config.rawRoot.appending(path: ".skipped").appending(path: lane)
            ]
            let occupiedStems = try Set(directories.flatMap { directory -> [String] in
                guard fileManager.fileExists(atPath: directory.path) else { return [] }
                return try fileManager.contentsOfDirectory(
                    at: directory, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]
                ).map { $0.deletingPathExtension().lastPathComponent.lowercased() }
            })
            // Different extensions share the same Markdown output name.
            let stem = source.deletingPathExtension().lastPathComponent
            let fileExtension = source.pathExtension
            var target = destination.appending(path: source.lastPathComponent)
            var suffix = 2
            while occupiedStems.contains(target.deletingPathExtension().lastPathComponent.lowercased()) {
                target = destination.appending(path: "\(stem)-\(suffix).\(fileExtension)")
                suffix += 1
            }
            try fileManager.copyItem(at: source, to: target)
            return target
        } catch {
            return nil
        }
    }
}

import Foundation

/// Copies dropped or imported files into the pipeline's drop folders.
///
/// Existing files of the same name are overwritten, so re-adding always
/// succeeds; whether a file is actually re-processed is decided downstream
/// by the manifest's content hash, not here.
enum DropStaging {
    static let documentExtensions: Set<String> = ["pdf", "md", "txt", "tex"]

    /// Copy supported documents (expanding folders) into the documents lane.
    static func stageDocuments(_ selection: [URL], config: AppConfig) -> (added: Int, failed: Bool) {
        var added = 0
        var failed = false
        for file in documentFiles(in: selection) {
            if copy(file, intoLane: "documents", config: config) {
                added += 1
            } else {
                failed = true
            }
        }
        return (added, failed)
    }

    /// Copy a provider's export files into its lane. Returns the count copied.
    static func importExport(_ provider: ExportProvider, files: [URL], config: AppConfig) -> Int {
        files.reduce(0) { copied, file in
            copy(file, intoLane: provider.lane, config: config) ? copied + 1 : copied
        }
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

    private static func copy(_ source: URL, intoLane lane: String, config: AppConfig) -> Bool {
        let fileManager = FileManager.default
        let destination = config.dropsRoot.appending(path: lane)
        do {
            try fileManager.createDirectory(at: destination, withIntermediateDirectories: true)
        } catch {
            return false
        }
        let target = destination.appending(path: source.lastPathComponent)
        do {
            if fileManager.fileExists(atPath: target.path) {
                try fileManager.removeItem(at: target)
            }
            try fileManager.copyItem(at: source, to: target)
            return true
        } catch {
            return false
        }
    }
}

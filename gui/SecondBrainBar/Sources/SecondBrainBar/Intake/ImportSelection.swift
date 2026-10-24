import Foundation

/// Expands only user-selected document folders. The backend copies and deduplicates files.
enum ImportSelection {
    static let documentExtensions: Set<String> = ["pdf", "md", "txt", "tex"]
    static func documentFiles(in selection: [URL]) -> [URL] {
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

}

struct ImportSubmission: Decodable {
    let job: JobResponse
    let files: [ImportFile]
}

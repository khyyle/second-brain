import Foundation

/// A readable row title: drop the path and extension, and the 8-char
/// disambiguation suffix the conversation parser appends to markdown names.
func cleanName(_ raw: String) -> String {
    let name = (raw as NSString).lastPathComponent
    let isMarkdown = name.lowercased().hasSuffix(".md")
    var stem = (name as NSString).deletingPathExtension
    if isMarkdown {
        stem = stem.replacingOccurrences(
            of: "-[0-9a-f]{8}$", with: "", options: .regularExpression
        )
    }
    return stem
}

func relativeTime(_ date: Date) -> String {
    let interval = Date().timeIntervalSince(date)
    if interval < 60      { return "now" }
    if interval < 3600    { return "\(Int(interval / 60))m" }
    if interval < 86_400  { return "\(Int(interval / 3600))h" }
    if interval < 604_800 { return "\(Int(interval / 86_400))d" }
    return "\(Int(interval / 604_800))w"
}

/// Format an elapsed duration compactly: "8s", "1m 23s".
func formatElapsed(_ seconds: TimeInterval) -> String {
    let s = Int(seconds)
    if s < 60 { return "\(s)s" }
    return "\(s / 60)m \(s % 60)s"
}

import Foundation

/// A snapshot of pipeline progress, read from the Python heartbeat file
/// at `~/second-brain/.status.json`.
struct PipelineStatus {
    let running: Bool
    let phase: String
    let current: Int
    let total: Int
    let updatedAt: Date
    let startedAt: Date
    let costUSD: Double

    /// A heartbeat older than this is treated as stale (process died
    /// without clearing it), so the UI won't show a phantom spinner.
    private static let staleAfter: TimeInterval = 20

    var isActive: Bool {
        running && Date().timeIntervalSince(updatedAt) < Self.staleAfter
    }

    /// Seconds since this run started.
    var elapsed: TimeInterval {
        max(0, Date().timeIntervalSince(startedAt))
    }

    /// Read and parse the heartbeat, returning nil if absent or invalid.
    static func read(from url: URL) -> PipelineStatus? {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let updated = (obj["updated_at"] as? String).flatMap { iso.date(from: $0) }
            ?? Date.distantPast
        let started = (obj["started_at"] as? String).flatMap { iso.date(from: $0) }
            ?? updated

        return PipelineStatus(
            running: obj["running"] as? Bool ?? false,
            phase: obj["phase"] as? String ?? "idle",
            current: obj["current"] as? Int ?? 0,
            total: obj["total"] as? Int ?? 0,
            updatedAt: updated,
            startedAt: started,
            costUSD: obj["cost_usd"] as? Double ?? 0
        )
    }
}

/// Format an elapsed duration compactly: "8s", "1m 23s".
func formatElapsed(_ seconds: TimeInterval) -> String {
    let s = Int(seconds)
    if s < 60 { return "\(s)s" }
    return "\(s / 60)m \(s % 60)s"
}

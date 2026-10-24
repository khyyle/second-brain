import Foundation

/// Shared payload for GET /desktop-state and each SSE snapshot event.
struct DesktopSnapshot: Decodable {
    let state: AppState
    let jobs: [JobDetail]
    let imports: [ImportFile]
    let triage: [TriageResponse]
    let grouping_plan: ClusterPlan?
    let build_actions: [BuildActionResponse]
    let compilation_model: String
}

/// SSE framing is independent of TCP chunks; a blank line completes an event.
struct ServerEventParser {
    struct Event { let name: String; let data: Data }
    private var name = "message"
    private var lines: [String] = []

    mutating func consume(_ line: String) -> Event? {
        if line.isEmpty {
            defer { name = "message"; lines.removeAll(keepingCapacity: true) }
            guard !lines.isEmpty else { return nil }
            return Event(name: name, data: Data(lines.joined(separator: "\n").utf8))
        }
        if line.hasPrefix(":") { return nil }
        let fields = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        var value = fields.count == 2 ? String(fields[1]) : ""
        if value.hasPrefix(" ") { value.removeFirst() }
        switch fields[0] {
        case "event": name = value
        case "data": lines.append(value)
        default: break
        }
        return nil
    }
}

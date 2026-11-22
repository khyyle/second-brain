import Foundation

/// A triage decision for one ingested source.
struct TriageRow: Identifiable, Hashable {
    let id: String           // raw path
    let displayName: String
    let decision: Decision
    let confidence: Double
    let reason: String

    enum Decision: String {
        case worthwhile
        case review
        case skip
    }
}

struct TriageResponse: Decodable {
    let raw_path: String
    let decision: String
    let confidence: Double
    let reason: String?
    let available: Bool
    var row: TriageRow? {
        guard let verdict = TriageRow.Decision(rawValue: decision) else { return nil }
        return TriageRow(id: raw_path, displayName: ((raw_path as NSString).lastPathComponent as NSString).deletingPathExtension,
                         decision: verdict, confidence: confidence, reason: reason ?? "")
    }
}

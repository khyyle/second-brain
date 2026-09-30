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

        var label: String {
            switch self {
            case .worthwhile: return "kept"
            case .review:     return "review"
            case .skip:       return "skipped"
            }
        }
    }
}

import Foundation

/// One submitted file or unpacked chat and its ingestion outcome.
struct QueueItem: Identifiable, Hashable {
    let id: String          // drop path or relative raw path
    let displayName: String
    let state: State
    let since: Date         // when it entered its current state (for elapsed)
    var outcome: Outcome? = nil

    enum Outcome: String {
        case staged
        case unpacked
        case kept
        case review = "needs review"
        case skipped
        case alreadyProcessed = "already processed"
    }

    enum State {
        case waiting        // in drops, not yet seen by the pipeline
        case processing     // currently being ingested
        case triaging       // an imported chat the local model is triaging
        case duplicate      // identical content already ingested elsewhere
        case failed         // ingestion failed
        case finished       // just done; shown briefly with where it went

        var label: String {
            switch self {
            case .waiting:    return "waiting"
            case .processing: return "processing"
            case .triaging:   return "triaging"
            case .duplicate:  return "already processed"
            case .failed:     return "failed"
            case .finished:   return "done"
            }
        }

        var isWorking: Bool { self == .processing || self == .triaging }
        var isFinished: Bool { self == .finished || self == .duplicate }
    }
}

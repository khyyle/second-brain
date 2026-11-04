import Foundation

/// Status for each local model process; a null entry means the worker is unavailable.
struct InferenceStatusResponse: Decodable {
    let worker_statuses: [String: InferenceWorkerStatus?]
}

struct InferenceWorkerStatus: Decodable {
    let model: String
    let phase: String
    /// Bytes downloaded or verified in the current preparation phase.
    let completed: Int64
    /// Expected file size, or zero when byte progress is unavailable.
    let total: Int64
    let error: String?
}

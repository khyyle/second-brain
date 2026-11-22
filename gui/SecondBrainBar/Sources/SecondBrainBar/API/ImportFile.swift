import Foundation

/// Import records returned by the backend, including pending filesystem copies.
struct ImportFile: Decodable {
    let file_path: String
    let status: String
    let updated_at: String
    let error: String?
    var queueItem: QueueItem {
        let state: QueueItem.State
        switch status {
        case "processing": state = .processing
        case "failed": state = .failed
        case "complete": state = .finished
        case "duplicate": state = .duplicate
        default: state = .waiting
        }
        return QueueItem(id: file_path, displayName: (file_path as NSString).lastPathComponent,
                         state: state, since: backendDate(updated_at) ?? Date())
    }
}

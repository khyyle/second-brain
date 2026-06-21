import Foundation

/// Display metadata for the drop zone (icon, label, hint). Files are routed
/// to a drop folder by content at copy time, not by this category.
enum Bin: String, CaseIterable, Identifiable {
    case inbox = "documents"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .inbox: return "Drop files or a folder"
        }
    }

    var hint: String {
        switch self {
        case .inbox: return "or click to browse"
        }
    }

    /// SF Symbol shown inside the drop zone.
    var iconName: String {
        switch self {
        case .inbox: return "tray.and.arrow.down"
        }
    }
}

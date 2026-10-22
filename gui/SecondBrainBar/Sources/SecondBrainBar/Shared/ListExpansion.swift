import Foundation

/// How far each list is expanded, kept for the session. Tab views are rebuilt
/// on every switch, so state held in a view would fold back each time.
final class ListExpansion: ObservableObject {
    @Published private var pageLimits: [String: Int] = [:]

    /// Rows a paginated list shows past its initial window; 0 means the window.
    func pageLimit(for key: String) -> Int { pageLimits[key] ?? 0 }

    func setPageLimit(_ limit: Int, for key: String) { pageLimits[key] = limit }
}

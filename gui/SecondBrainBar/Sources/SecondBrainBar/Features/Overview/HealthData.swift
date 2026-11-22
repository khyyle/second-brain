import Foundation

/// One flagged item within a category. `page` is the wiki page stem to open, or
/// nil when the item is not page-backed (a gap points at a page that does not
/// exist). `detail` is an optional note shown beside it, e.g. a gap's reference
/// count. `pair` is a second page stem for items that flag a pair of pages
/// (a possible duplicate), rendered as its own clickable line.
struct HealthItem: Identifiable {
    let id = UUID()
    let text: String
    let page: String?
    let detail: String?
    let pair: String?
}

/// A single check and the items it flagged. `section` places it under either
/// "improve" (growth opportunities) or "health" (defects). An empty `items`
/// means the check passed.
struct HealthCategory: Identifiable {
    let key: String
    let label: String
    let section: String
    let items: [HealthItem]
    var id: String { key }
    var count: Int { items.count }
}

/// The wiki's overview, as a list of checks split across sections.
struct WikiHealth {
    let healthy: Bool
    let categories: [HealthCategory]

    func categories(in section: String) -> [HealthCategory] {
        categories.filter { $0.section == section }
    }
}

struct WikiHealthResponse: Decodable {
    let orphan_pages: [String]
    let gap_links: [CountedPage]
    let possible_duplicates: [DuplicatePages]
    let oversized_pages: [CountedPage]
    let undersized_pages: [CountedPage]
    let missing_frontmatter: [String]
    var presentation: WikiHealth {
        func page(_ name: String, detail: String? = nil) -> HealthItem {
            HealthItem(text: name, page: name, detail: detail, pair: nil)
        }
        let categories = [
            HealthCategory(key: "orphan_pages", label: "Unlinked pages", section: "improve", items: orphan_pages.map { page($0) }),
            HealthCategory(key: "gap_links", label: "Missing topics", section: "improve", items: gap_links.map { HealthItem(text: $0.name, page: nil, detail: "\($0.count) links", pair: nil) }),
            HealthCategory(key: "possible_duplicates", label: "Possible duplicates", section: "health", items: possible_duplicates.map { HealthItem(text: $0.first, page: $0.first, detail: nil, pair: $0.second) }),
            HealthCategory(key: "oversized_pages", label: "Long pages", section: "health", items: oversized_pages.map { page($0.name, detail: "\($0.count) words") }),
            HealthCategory(key: "undersized_pages", label: "Short pages", section: "improve", items: undersized_pages.map { page($0.name, detail: "\($0.count) words") }),
            HealthCategory(key: "missing_frontmatter", label: "Missing metadata", section: "health", items: missing_frontmatter.map { page($0) })
        ]
        return WikiHealth(healthy: categories.allSatisfy { $0.items.isEmpty }, categories: categories)
    }
}
struct CountedPage: Decodable {
    let name: String
    let count: Int
    init(from decoder: Decoder) throws {
        var fields = try decoder.unkeyedContainer()
        name = try fields.decode(String.self)
        count = try fields.decode(Int.self)
    }
}
struct DuplicatePages: Decodable {
    let first: String
    let second: String
    init(from decoder: Decoder) throws {
        var fields = try decoder.unkeyedContainer()
        first = try fields.decode(String.self)
        second = try fields.decode(String.self)
    }
}
struct WikiPageResponse: Decodable { let path: String }

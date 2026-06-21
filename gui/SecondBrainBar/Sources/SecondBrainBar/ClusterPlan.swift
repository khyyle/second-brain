import Foundation

/// The build plan the Python `preview-clusters` command writes to
/// `.clusters.json`: how staged sources group into compile units and what
/// that would cost. The menu bar reads it to show the grouped Build view;
/// the compiler reads the same artifact so the build matches the preview.
struct ClusterPlan: Decodable {
    let generatedAt: String
    let algorithm: String
    let enabled: Bool
    let sourceCount: Int
    let groupCount: Int
    let estimatedCostUSD: Double
    let groups: [ClusterGroup]

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case algorithm
        case enabled
        case sourceCount = "source_count"
        case groupCount = "group_count"
        case estimatedCostUSD = "estimated_cost_usd"
        case groups
    }

    /// The raw paths of every source the plan covers.
    var memberPaths: Set<String> {
        Set(groups.flatMap { group in group.members.map(\.rel) })
    }

    /// Load the plan artifact, or nil if absent or unreadable.
    static func load(_ url: URL) -> ClusterPlan? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ClusterPlan.self, from: data)
    }
}

struct ClusterGroup: Decodable, Identifiable {
    let id: String
    let title: String
    let members: [ClusterMember]
    let estimatedCostUSD: Double

    enum CodingKeys: String, CodingKey {
        case id, title, members
        case estimatedCostUSD = "estimated_cost_usd"
    }
}

struct ClusterMember: Decodable, Hashable {
    let rel: String
    let bytes: Int64

    var displayName: String { (rel as NSString).lastPathComponent }
}

/// User corrections to the grouping, written to `.cluster-overrides.json`
/// for the build to honor: sources to pop out of their group, and groups
/// to split into one page per source.
struct ClusterOverrides: Codable {
    var excluded: [String]
    var splitGroups: [String]

    enum CodingKeys: String, CodingKey {
        case excluded
        case splitGroups = "split_groups"
    }

    static let empty = ClusterOverrides(excluded: [], splitGroups: [])

    func isExcluded(_ rel: String) -> Bool { excluded.contains(rel) }
    func isSplit(_ groupID: String) -> Bool { splitGroups.contains(groupID) }

    mutating func toggleExcluded(_ rel: String) {
        if let index = excluded.firstIndex(of: rel) {
            excluded.remove(at: index)
        } else {
            excluded.append(rel)
        }
    }

    mutating func toggleSplit(_ groupID: String) {
        if let index = splitGroups.firstIndex(of: groupID) {
            splitGroups.remove(at: index)
        } else {
            splitGroups.append(groupID)
        }
    }

    static func load(_ url: URL) -> ClusterOverrides {
        guard let data = try? Data(contentsOf: url),
              let decoded = try? JSONDecoder().decode(ClusterOverrides.self, from: data)
        else { return .empty }
        return decoded
    }

    func write(to url: URL) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted]
        guard let data = try? encoder.encode(self) else { return }
        try? data.write(to: url, options: .atomic)
    }
}

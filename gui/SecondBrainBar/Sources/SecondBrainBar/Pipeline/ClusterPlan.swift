import Foundation

/// Reviewed grouping returned by the API; the backend snapshots it for builds.
struct ClusterPlan: Decodable {
    let generatedAt: String
    let algorithm: String
    let enabled: Bool
    let sourceCount: Int
    let groupCount: Int
    let groups: [ClusterGroup]
    let overrides: ClusterOverrides

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case algorithm
        case enabled
        case sourceCount = "source_count"
        case groupCount = "group_count"
        case groups, overrides
    }

    /// The raw paths of every source the plan covers.
    var memberPaths: Set<String> {
        Set(groups.flatMap { group in group.members.map(\.rel) })
    }


}

struct ClusterGroup: Decodable, Identifiable {
    let id: String
    let title: String
    let members: [ClusterMember]
    /// Estimated cost of building the group with the chosen model, or nil when unknown.
    let estimatedCostUSD: Double?

    enum CodingKeys: String, CodingKey {
        case id, title, members
        case estimatedCostUSD = "estimated_cost_usd"
    }

    func containsAny(of rawPaths: Set<String>) -> Bool {
        members.contains { rawPaths.contains($0.rel) }
    }

    /// The group limited to members still staged, or nil once none are.
    func narrowed(to rawPaths: Set<String>) -> ClusterGroup? {
        let staged = members.filter { rawPaths.contains($0.rel) }
        guard !staged.isEmpty else { return nil }
        return ClusterGroup(id: id, title: title, members: staged, estimatedCostUSD: estimatedCostUSD)
    }
}

struct ClusterMember: Decodable, Hashable {
    let rel: String
    let bytes: Int64

    var displayName: String { (rel as NSString).lastPathComponent }
}

/// User corrections saved through PUT /grouping-plan/overrides: sources to pop out of their group, and groups
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

}

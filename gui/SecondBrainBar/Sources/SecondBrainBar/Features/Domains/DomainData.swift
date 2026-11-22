import Foundation

struct DomainInfo: Decodable, Identifiable {
    let name: String
    let pageCount: Int
    let inSchema: Bool
    var id: String { name }
    enum CodingKeys: String, CodingKey { case name; case pageCount = "page_count"; case inSchema = "in_schema" }
}

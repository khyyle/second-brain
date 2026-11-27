import Foundation

struct PreferencesResponse: Decodable {
    let compilation: CompilationPreferences
    let triage: TriagePreferences
}
struct CompilationPreferences: Decodable {
    let provider: String
    let model: String
    let max_cost_per_build_usd: Double
}
struct TriagePreferences: Decodable {
    let enabled: Bool
    let profile: String
}
struct ApplicationCatalog: Decodable {
    let file_types: [String]
    let triage_profiles: [String]
}
struct PiProviderResponse: Decodable, Identifiable {
    let id: String
    let name: String
    let login_methods: [String]
}
struct ModelChoiceResponse: Decodable, Identifiable, Equatable {
    let id: String
    let name: String
}
struct IntegrationStatus: Decodable { let configured: Bool; let message: String }

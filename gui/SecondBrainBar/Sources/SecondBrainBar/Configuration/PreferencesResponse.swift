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
    let providers: [ProviderInfo]
    let file_types: [String]
    let triage_profiles: [String]
}
struct ProviderInfo: Decodable, Identifiable {
    let id: String
    let name: String
    let models: [ModelInfo]
}
struct ModelInfo: Decodable, Identifiable { let id: String }
struct CredentialStatus: Decodable { let configured: Bool }
struct IntegrationStatus: Decodable { let configured: Bool; let message: String }
struct OllamaHealth: Decodable { let healthy: Bool; let reachable: Bool; let message: String }

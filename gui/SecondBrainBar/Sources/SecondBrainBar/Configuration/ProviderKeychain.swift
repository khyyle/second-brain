import Foundation
import Security

/// Provider secrets are shared with the Python backend via the macOS login
/// Keychain. They never enter HTTP payloads, preferences, or environment files.
enum ProviderKeychain {
    static let service = "com.second-brain.providers"
    static func save(_ key: String, provider: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: provider]
        let value = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { throw BackendError(message: "Enter an API key before saving.") }
        let attributes = [kSecValueData as String: Data(value.utf8)]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw BackendError(message: "Keychain: \(SecCopyErrorMessageString(status, nil) as String? ?? String(status))")
        }
    }
    static func remove(provider: String) throws {
        let status = SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                                   kSecAttrService as String: service, kSecAttrAccount as String: provider] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw BackendError(message: "Keychain: \(SecCopyErrorMessageString(status, nil) as String? ?? String(status))")
        }
    }
}

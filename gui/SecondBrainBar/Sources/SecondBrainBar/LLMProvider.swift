import Foundation

/// Cloud LLM providers the compilation agent can run on. Mirrors the
/// catalog in `second_brain/llm_providers.py`. Should be kept in sync with python.
/// Re-hardcoded here since the set of supported compilation models is unlikely to grow
enum LLMProvider: String, CaseIterable, Identifiable {
    case anthropic
    case deepseek

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .anthropic: return "Anthropic"
        case .deepseek: return "DeepSeek"
        }
    }

    /// The `.env` variable holding this provider's API key.
    var envKeyName: String {
        switch self {
        case .anthropic: return "ANTHROPIC_API_KEY"
        case .deepseek: return "DEEPSEEK_API_KEY"
        }
    }

    var keyPlaceholder: String {
        switch self {
        case .anthropic: return "sk-ant-…"
        case .deepseek: return "sk-…"
        }
    }

    /// Selectable models (model id written to config, short UI label)
    var models: [(id: String, label: String)] {
        switch self {
        case .anthropic:
            return [("claude-sonnet-4-6", "Sonnet")]
        case .deepseek:
            return [("deepseek-v4-pro", "Pro"), ("deepseek-v4-flash", "Flash")]
        }
    }

    var defaultModel: String { models[0].id }

    /// Cache-miss (input, output) USD per 1M tokens for a model, for a
    /// pre-build cost estimate. Mirrors _MODEL_PRICES in
    /// second_brain/llm_providers.py. Unknown models fall back to Claude.
    static func modelPrice(_ model: String) -> (input: Double, output: Double) {
        switch model {
        case "deepseek-v4-flash": return (0.14, 0.28)
        case "deepseek-v4-pro": return (0.435, 0.87)
        default: return (3.0, 15.0)  // claude-sonnet-4-6
        }
    }
}

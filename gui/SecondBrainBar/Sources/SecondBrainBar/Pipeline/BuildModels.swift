import Foundation

/// What the provider saved in Settings offers the Build tab.
enum BuildModels: Equatable {
    case loading
    /// The provider has no saved login.
    case disconnected
    /// The connected account's models. An account can be connected and offer none.
    case available([ModelChoiceResponse])
    case failed(String)
}

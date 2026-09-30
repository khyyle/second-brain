import SwiftUI

/// Menu bar entry point.
@main
struct SecondBrainBarApp: App {
    @NSApplicationDelegateAdaptor(StatusBarController.self) private var controller

    var body: some Scene {
        Settings { EmptyView() }
    }
}

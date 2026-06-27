import SwiftUI

/// Visual language for the menu-bar UI: a clean, modern dark theme on
/// a warm charcoal base with a single terracotta accent. Typography is
/// the native system font throughout, so it reads as a proper macOS
/// utility rather than a styled novelty.
enum Theme {
    /// Authoritative color palette. Hex values live here only; everything else
    /// in the app refers to these names, never a raw `Color(red:...)`.
    enum Colors {
        static let panelChrome   = Color(red: 0.106, green: 0.094, blue: 0.086) // #1b1816
        static let background    = Color(red: 0.086, green: 0.078, blue: 0.071) // #161412
        static let surface       = Color(red: 0.118, green: 0.106, blue: 0.094) // #1e1b18
        static let surfaceHover   = Color(red: 0.153, green: 0.137, blue: 0.125) // #272320
        static let stroke        = Color(red: 0.196, green: 0.180, blue: 0.161) // #322e29
        static let textPrimary   = Color(red: 0.925, green: 0.914, blue: 0.890) // #ece9e3
        static let textSecondary = Color(red: 0.604, green: 0.576, blue: 0.541) // #9a938a
        static let textTertiary  = Color(red: 0.373, green: 0.349, blue: 0.314) // #5f5950
        static let accent        = Color(red: 0.878, green: 0.380, blue: 0.235) // #e0613c
        static let accentAmber   = Color(red: 0.910, green: 0.635, blue: 0.239) // #e8a23d
        static let success       = Color(red: 0.455, green: 0.663, blue: 0.416) // #74a96a
        static let danger        = Color(red: 0.878, green: 0.341, blue: 0.306) // #e0574e
    }

    enum Font {
        static func wordmark(_ size: CGFloat) -> SwiftUI.Font {
            .system(size: size, weight: .semibold)
        }

        static func body(_ size: CGFloat, weight: SwiftUI.Font.Weight = .regular) -> SwiftUI.Font {
            .system(size: size, weight: weight)
        }

        /// Section eyebrow ("RECENT"), used with uppercased text + tracking.
        static func eyebrow(_ size: CGFloat) -> SwiftUI.Font {
            .system(size: size, weight: .semibold)
        }

        /// Monospaced digits for timestamps so they don't jitter as they tick.
        static func meta(_ size: CGFloat) -> SwiftUI.Font {
            .system(size: size, weight: .regular).monospacedDigit()
        }
    }

    enum Metric {
        static let popoverWidth: CGFloat = 340
        static let corner: CGFloat       = 10
        static let cornerSmall: CGFloat  = 7
        static let zoneHeight: CGFloat   = 78
        static let listHeight: CGFloat   = 208
    }

    /// Subtle top-lit gradient that gives the popover depth without
    /// resorting to a flat fill.
    static var backgroundGradient: LinearGradient {
        LinearGradient(
            colors: [Colors.panelChrome, Colors.background],
            startPoint: .top,
            endPoint: .bottom
        )
    }
}

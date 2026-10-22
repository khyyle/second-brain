import SwiftUI

/// The small in-progress spinner shown in a row's leading icon slot.
struct RowSpinner: View {
    var body: some View {
        ProgressView().controlSize(.small).scaleEffect(0.6)
    }
}
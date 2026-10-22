import Combine
import Foundation

/// One shared once-a-second tick for views that poll files. A timer created in
/// a view's body is replaced every time the view re-renders, and the panel
/// re-renders every second, so such a timer never gets to fire.
enum Ticker {
    static let everySecond = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
}

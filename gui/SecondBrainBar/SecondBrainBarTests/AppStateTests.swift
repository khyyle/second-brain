import XCTest
@testable import SecondBrainBar

final class AppStateTests: XCTestCase {
    private func decode(_ state: [String: Any]) throws -> AppState {
        try JSONDecoder().decode(AppState.self, from: JSONSerialization.data(withJSONObject: state))
    }

    /// A model without a known price has no estimate. That is not an estimate of zero.
    func testEstimatesAreFiguresOrUnknown() throws {
        var state = APIFixtures.state()
        XCTAssertNil(try decode(state).estimatedCostUSD)

        state["estimated_cost_usd"] = 1.25
        state["staged"] = [
            ["rel": "priced.md", "estimated_cost_usd": 0.5],
            ["rel": "unknown.md", "estimated_cost_usd": NSNull()],
        ]
        let priced = try decode(state)
        XCTAssertEqual(priced.estimatedCostUSD, 1.25)
        XCTAssertEqual(priced.staged.map(\.estimatedCostUSD), [0.5, nil])
    }
}

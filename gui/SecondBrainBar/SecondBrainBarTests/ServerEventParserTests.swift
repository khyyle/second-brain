import XCTest
@testable import SecondBrainBar

final class ServerEventParserTests: XCTestCase {
    func testKeepalivesAndMultilineMessages() throws {
        var parser = ServerEventParser()
        XCTAssertNil(parser.consume(": keepalive"))
        XCTAssertNil(parser.consume(""))
        XCTAssertNil(parser.consume("event: snapshot"))
        XCTAssertNil(parser.consume("data: {"))
        XCTAssertNil(parser.consume("data: \"jobs\": []}"))
        let event = try XCTUnwrap(parser.consume(""))
        XCTAssertEqual(event.name, "snapshot")
        XCTAssertEqual(String(decoding: event.data, as: UTF8.self), "{\n\"jobs\": []}")
        XCTAssertNil(parser.consume("data: next"))
        XCTAssertEqual(parser.consume("")?.name, "message")
    }
}

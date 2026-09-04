import XCTest
@testable import FinalCutBridge

final class ProtocolTests: XCTestCase {
    func testAllowedActionDecodes() throws {
        let data = #"{"protocolVersion":1,"action":"probe","sessionRoot":"/tmp/session","payload":{}}"#.data(using: .utf8)!
        let request = try StrictProtocol.decodeRequest(data)
        XCTAssertEqual(request.action, .probe)
    }

    func testUnknownTopLevelKeyFails() {
        let data = #"{"protocolVersion":1,"action":"probe","sessionRoot":"/tmp/session","payload":{},"shell":"id"}"#.data(using: .utf8)!
        XCTAssertThrowsError(try StrictProtocol.decodeRequest(data))
    }

    func testUnknownActionFails() {
        let data = #"{"protocolVersion":1,"action":"run_script","sessionRoot":"/tmp/session","payload":{}}"#.data(using: .utf8)!
        XCTAssertThrowsError(try StrictProtocol.decodeRequest(data))
    }

    func testNonDictionaryPayloadFails() {
        let data = #"{"protocolVersion":1,"action":"probe","sessionRoot":"/tmp/session","payload":[]}"#.data(using: .utf8)!
        XCTAssertThrowsError(try StrictProtocol.decodeRequest(data))
    }

    func testBooleanProtocolVersionFails() {
        let data = #"{"protocolVersion":true,"action":"probe","sessionRoot":"/tmp/session","payload":{}}"#.data(using: .utf8)!
        XCTAssertThrowsError(try StrictProtocol.decodeRequest(data))
    }
}

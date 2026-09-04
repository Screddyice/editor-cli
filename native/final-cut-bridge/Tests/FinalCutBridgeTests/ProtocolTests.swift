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

    func testDelayedOversizedInputProducesOneErrorResponse() throws {
        let executable = Bundle(for: Self.self).bundleURL
            .deletingLastPathComponent()
            .appendingPathComponent("FinalCutBridge")
        XCTAssertTrue(FileManager.default.isExecutableFile(atPath: executable.path))

        let process = Process()
        let input = Pipe()
        let output = Pipe()
        process.executableURL = executable
        process.standardInput = input
        process.standardOutput = output
        process.standardError = Pipe()
        try process.run()

        let request = #"{"protocolVersion":1,"action":"probe","sessionRoot":"/tmp/session","payload":{}}"#.data(using: .utf8)!
        input.fileHandleForWriting.write(request)
        Thread.sleep(forTimeInterval: 0.05)
        XCTAssertTrue(process.isRunning)

        input.fileHandleForWriting.write(Data(repeating: 0x20, count: 1_048_576))
        try input.fileHandleForWriting.close()
        process.waitUntilExit()

        let responseData = output.fileHandleForReading.readDataToEndOfFile()
        let responseText = try XCTUnwrap(String(data: responseData, encoding: .utf8))
        XCTAssertEqual(responseText.filter { $0 == "\n" }.count, 1)
        let response = try XCTUnwrap(try JSONSerialization.jsonObject(with: responseData) as? [String: Any])
        XCTAssertEqual(response["ok"] as? Bool, false)
        XCTAssertEqual(process.terminationStatus, 1)
    }
}

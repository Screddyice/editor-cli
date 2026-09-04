import Foundation

private let maximumInputBytes = 1_048_576

private func writeResponse(_ response: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])) ?? Data("{\"ok\":false,\"error\":\"Unable to encode response\"}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

private func readRequest() throws -> Data {
    // Read one sentinel byte beyond the accepted limit so oversized input is
    // rejected without buffering an unbounded stdin stream.
    let data = try FileHandle.standardInput.read(upToCount: maximumInputBytes + 1) ?? Data()
    guard data.count <= maximumInputBytes else {
        throw ProtocolError.inputTooLarge
    }
    return data
}

private func dispatch(_ request: Request) -> [String: Any] {
    [
        "ok": true,
        "result": [
            "protocolVersion": request.protocolVersion,
            "action": request.action.rawValue,
        ],
    ]
}

do {
    let request = try StrictProtocol.decodeRequest(readRequest())
    writeResponse(dispatch(request))
} catch {
    writeResponse([
        "ok": false,
        "error": (error as? LocalizedError)?.errorDescription ?? "Protocol error.",
    ])
    exit(1)
}

import Foundation

private let maximumInputBytes = 1_048_576
private let inputReadChunkBytes = 64 * 1024

private func writeResponse(_ response: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])) ?? Data("{\"ok\":false,\"error\":\"Unable to encode response\"}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

private func readRequest() throws -> Data {
    var retained = Data()

    while retained.count <= maximumInputBytes {
        let remainingCapacity = maximumInputBytes + 1 - retained.count
        let readCount = min(inputReadChunkBytes, remainingCapacity)
        guard let chunk = try FileHandle.standardInput.read(upToCount: readCount), !chunk.isEmpty else {
            break
        }
        retained.append(chunk)
        if retained.count > maximumInputBytes {
            throw ProtocolError.inputTooLarge
        }
    }

    return retained
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

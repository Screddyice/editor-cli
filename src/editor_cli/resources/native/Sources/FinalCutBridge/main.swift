import Foundation

private let maximumInputBytes = 1_048_576
private let inputReadChunkBytes = 64 * 1024

private func writeResponse(_ response: [String: Any]) {
  let data =
    (try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys]))
    ?? Data("{\"ok\":false,\"error\":\"Unable to encode response\"}".utf8)
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

private func encodedObject<Value: Encodable>(_ value: Value) throws -> Any {
  let data = try JSONEncoder().encode(value)
  return try JSONSerialization.jsonObject(with: data)
}

private func encodedDictionary<Value: Encodable>(_ value: Value) throws -> [String: Any] {
  guard let dictionary = try encodedObject(value) as? [String: Any] else {
    throw ProtocolError.invalidPayload
  }
  return dictionary
}

private func result(_ fields: [String: Any]) -> [String: Any] {
  var versioned = fields
  versioned["protocolVersion"] = StrictProtocol.version
  return ["ok": true, "result": versioned]
}

private func requestPermissions() throws -> [String: Any] {
  let requested = try FinalCutPermissionRequester(
    system: LiveFinalCutSystem(),
    permissions: LiveFinalCutPermissionTransport()
  ).run()
  return [
    "ok": true,
    "result": [
      "accessibilityTrusted": requested.accessibilityTrusted,
      "automationAuthorized": requested.automationAuthorized,
    ],
  ]
}

private func dispatch(_ request: Request) throws -> [String: Any] {
  _ = try SessionPath(root: request.sessionRoot)
  let payload = try ActionPayload.decode(request)
  let system = LiveFinalCutSystem(sessionRoot: request.sessionRoot)
  let actions = Actions(system: system)

  switch payload {
  case .probe:
    let probe = try FinalCutProbe(system: system).run()
    return result([
      "bundleIdentifier": probe.bundleIdentifier,
      "version": probe.version,
      "ready": probe.ready,
      "accessibilityTrusted": probe.accessibilityTrusted,
      "automationAuthorized": probe.automationAuthorized,
      "libraryNames": probe.libraryNames,
      "activeProject": try probe.activeProject.map(encodedObject) ?? NSNull(),
      "dialogs": try encodedObject(probe.blockingDialogs),
    ])
  case .duplicateProject(let expected, let name, let timeout):
    return result([
      "project": try encodedObject(
        actions.duplicateProject(expected: expected, name: name, timeout: timeout)
      )
    ])
  case .exportXML(let expected, let output, let timeout):
    let receipt = try actions.exportXML(expected: expected, output: output, timeout: timeout)
    return result(try encodedDictionary(receipt))
  case .importXML(let expected, let source, let timeout):
    return result([
      "project": try encodedObject(
        actions.importXML(expected: expected, source: source, timeout: timeout)
      )
    ])
  case .openProject(let expected, let timeout):
    return result([
      "project": try encodedObject(
        actions.openProject(expected: expected, timeout: timeout)
      )
    ])
  case .sharePreview(let expected, let output, let timeout):
    let receipt = try actions.sharePreview(
      expected: expected, output: output, timeout: timeout
    )
    return result(try encodedDictionary(receipt))
  case .inspectDialogs:
    return result(["dialogs": try encodedObject(actions.inspectDialogs())])
  }
}

if Array(CommandLine.arguments.dropFirst()) == ["--request-permissions"] {
  do {
    writeResponse(try requestPermissions())
  } catch {
    writeResponse([
      "ok": false,
      "error": (error as? LocalizedError)?.errorDescription ?? "Permission request failed.",
    ])
    exit(1)
  }
} else {
  do {
    let request = try StrictProtocol.decodeRequest(readRequest())
    writeResponse(try dispatch(request))
  } catch {
    writeResponse([
      "ok": false,
      "error": (error as? LocalizedError)?.errorDescription ?? "Protocol error.",
    ])
    exit(1)
  }
}

import Foundation

enum Action: String, Codable, CaseIterable {
    case probe
    case duplicateProject = "duplicate_project"
    case exportXML = "export_xml"
    case importXML = "import_xml"
    case openProject = "open_project"
    case sharePreview = "share_preview"
    case inspectDialogs = "inspect_dialogs"
}

struct Request {
    let protocolVersion: Int
    let action: Action
    let sessionRoot: String
    let payload: [String: Any]
}

enum ProtocolError: Error, LocalizedError {
    case invalidJSON
    case invalidTopLevel
    case unexpectedKeys
    case invalidProtocolVersion
    case invalidAction
    case invalidSessionRoot
    case invalidPayload
    case inputTooLarge

    var errorDescription: String? {
        switch self {
        case .invalidJSON: "Request must be valid JSON."
        case .invalidTopLevel: "Request must be a JSON object."
        case .unexpectedKeys: "Request keys must exactly match the protocol schema."
        case .invalidProtocolVersion: "Unsupported protocol version."
        case .invalidAction: "Unsupported action."
        case .invalidSessionRoot: "sessionRoot must be a string."
        case .invalidPayload: "payload must be a JSON object."
        case .inputTooLarge: "Request exceeds the 1 MiB input limit."
        }
    }
}

enum StrictProtocol {
    static let version = 1
    static let requestKeys: Set<String> = ["protocolVersion", "action", "sessionRoot", "payload"]

    static func decodeRequest(_ data: Data) throws -> Request {
        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data, options: [])
        } catch {
            throw ProtocolError.invalidJSON
        }

        guard let dictionary = object as? [String: Any] else {
            throw ProtocolError.invalidTopLevel
        }
        guard Set(dictionary.keys) == requestKeys else {
            throw ProtocolError.unexpectedKeys
        }
        guard let versionNumber = dictionary["protocolVersion"] as? NSNumber,
              CFGetTypeID(versionNumber) != CFBooleanGetTypeID(),
              versionNumber.intValue == version,
              versionNumber.doubleValue == Double(version) else {
            throw ProtocolError.invalidProtocolVersion
        }
        guard let actionValue = dictionary["action"] as? String,
              let action = Action(rawValue: actionValue) else {
            throw ProtocolError.invalidAction
        }
        guard let sessionRoot = dictionary["sessionRoot"] as? String else {
            throw ProtocolError.invalidSessionRoot
        }
        guard let payload = dictionary["payload"] as? [String: Any] else {
            throw ProtocolError.invalidPayload
        }

        return Request(
            protocolVersion: versionNumber.intValue,
            action: action,
            sessionRoot: sessionRoot,
            payload: payload
        )
    }
}

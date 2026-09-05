import AppKit
import ApplicationServices
import Foundation

struct ProjectIdentity: Codable, Equatable {
  let library: String
  let event: String
  let project: String
  let duration: TimeInterval

  enum CodingKeys: String, CodingKey {
    case library
    case event
    case project
    case duration = "duration_seconds"
  }

  func withProject(_ name: String) -> ProjectIdentity {
    ProjectIdentity(library: library, event: event, project: name, duration: duration)
  }
}

struct BlockingDialog: Codable, Equatable {
  let role: String
  let title: String
}

func sanitizedDialog(_ dialog: BlockingDialog) -> BlockingDialog {
  BlockingDialog(role: sanitizeDialogText(dialog.role), title: sanitizeDialogText(dialog.title))
}

private func sanitizeDialogText(_ value: String) -> String {
  let scalars = value.unicodeScalars.map { scalar in
    CharacterSet.controlCharacters.contains(scalar) ? UnicodeScalar(0x20)! : scalar
  }
  return String(String.UnicodeScalarView(scalars))
    .split(whereSeparator: { $0.isWhitespace })
    .joined(separator: " ")
    .prefix(256)
    .description
}

struct ActionFileSnapshot: Equatable {
  let size: UInt64
  let modifiedAt: TimeInterval
}

struct ExportReceipt: Codable, Equatable {
  let kind: String
  let project: ProjectIdentity
  let output: String
}

struct ShareReceipt: Codable, Equatable {
  let kind: String
  let project: ProjectIdentity
  let output: String
}

enum FinalCutMenu {
  static let duplicate = ["File", "Duplicate Project As..."]
  static let exportXML = ["File", "Export XML..."]
  static let share = ["File", "Share", "Export File (Default)..."]
}

enum FinalCutConfirmation: Equatable {
  case duplicate
  case exportXML
  case shareNext
  case shareSave
}

enum FinalCutActionError: Error, Equatable, LocalizedError {
  case accessibilityNotTrusted
  case invalidIdentity
  case invalidName
  case invalidPath
  case invalidTimeout
  case outputAlreadyExists
  case identityMismatch
  case projectNotFound
  case ambiguousProject
  case blockingDialog
  case timedOut

  var errorDescription: String? {
    switch self {
    case .accessibilityNotTrusted: "Accessibility permission is not granted."
    case .invalidIdentity: "Project identity fields must be non-empty and duration must be finite."
    case .invalidName: "Project name must be a non-empty single line."
    case .invalidPath: "Path must resolve below the active session root."
    case .invalidTimeout: "Action timeout must be between 0 and 3,600 seconds."
    case .outputAlreadyExists: "Refusing to replace an existing session output."
    case .identityMismatch: "Final Cut project identity did not match the exact expected identity."
    case .projectNotFound: "The exact Final Cut project was not found."
    case .ambiguousProject: "More than one Final Cut project matched the exact identity."
    case .blockingDialog: "Final Cut displayed a blocking dialog."
    case .timedOut: "Final Cut action timed out before its postcondition completed."
    }
  }
}

enum ActionPayload {
  case probe
  case duplicateProject(ProjectIdentity, String, TimeInterval)
  case exportXML(ProjectIdentity, String, TimeInterval)
  case importXML(ProjectIdentity, String, TimeInterval)
  case openProject(ProjectIdentity, TimeInterval)
  case sharePreview(ProjectIdentity, String, TimeInterval)
  case inspectDialogs

  static func decode(_ request: Request) throws -> ActionPayload {
    switch request.action {
    case .probe:
      try requireKeys(request.payload, expected: [])
      return .probe
    case .duplicateProject:
      try requireKeys(request.payload, expected: ["expected", "name", "timeout"])
      guard let name = request.payload["name"] as? String else {
        throw ProtocolError.invalidPayload
      }
      return .duplicateProject(
        try identity(request.payload["expected"]), name, try timeout(request.payload["timeout"])
      )
    case .exportXML:
      try requireKeys(request.payload, expected: ["expected", "output", "timeout"])
      guard let output = request.payload["output"] as? String else {
        throw ProtocolError.invalidPayload
      }
      return .exportXML(
        try identity(request.payload["expected"]), output, try timeout(request.payload["timeout"])
      )
    case .importXML:
      try requireKeys(request.payload, expected: ["expected", "source", "timeout"])
      guard let source = request.payload["source"] as? String else {
        throw ProtocolError.invalidPayload
      }
      return .importXML(
        try identity(request.payload["expected"]), source, try timeout(request.payload["timeout"])
      )
    case .openProject:
      try requireKeys(request.payload, expected: ["expected", "timeout"])
      return .openProject(
        try identity(request.payload["expected"]), try timeout(request.payload["timeout"])
      )
    case .sharePreview:
      try requireKeys(request.payload, expected: ["expected", "output", "timeout"])
      guard let output = request.payload["output"] as? String else {
        throw ProtocolError.invalidPayload
      }
      return .sharePreview(
        try identity(request.payload["expected"]), output, try timeout(request.payload["timeout"])
      )
    case .inspectDialogs:
      try requireKeys(request.payload, expected: [])
      return .inspectDialogs
    }
  }

  private static func identity(_ value: Any?) throws -> ProjectIdentity {
    guard let dictionary = value as? [String: Any] else {
      throw ProtocolError.invalidPayload
    }
    try requireKeys(
      dictionary, expected: ["library", "event", "project", "duration_seconds"]
    )
    guard let library = dictionary["library"] as? String,
      let event = dictionary["event"] as? String,
      let project = dictionary["project"] as? String
    else {
      throw ProtocolError.invalidPayload
    }
    return ProjectIdentity(
      library: library,
      event: event,
      project: project,
      duration: try number(dictionary["duration_seconds"])
    )
  }

  private static func timeout(_ value: Any?) throws -> TimeInterval {
    try number(value)
  }

  private static func number(_ value: Any?) throws -> Double {
    guard let number = value as? NSNumber,
      CFGetTypeID(number) != CFBooleanGetTypeID(),
      number.doubleValue.isFinite
    else {
      throw ProtocolError.invalidPayload
    }
    return number.doubleValue
  }

  private static func requireKeys(
    _ dictionary: [String: Any], expected: Set<String>
  ) throws {
    guard Set(dictionary.keys) == expected else {
      throw ProtocolError.invalidPayload
    }
  }
}

struct SessionPath {
  private let root: URL

  init(root: String) throws {
    guard root.hasPrefix("/") else {
      throw FinalCutActionError.invalidPath
    }
    let rootURL = URL(fileURLWithPath: root).standardizedFileURL.resolvingSymlinksInPath()
    guard rootURL.path.hasPrefix("/"), rootURL.path != "/" else {
      throw FinalCutActionError.invalidPath
    }
    self.root = rootURL
  }

  func output(_ path: String) throws -> String {
    try contained(path)
  }

  func input(_ path: String) throws -> String {
    try contained(path)
  }

  private func contained(_ path: String) throws -> String {
    guard path.hasPrefix("/") else {
      throw FinalCutActionError.invalidPath
    }
    let candidate = resolveExistingAncestors(of: URL(fileURLWithPath: path).standardizedFileURL)
    let rootComponents = root.pathComponents
    let candidateComponents = candidate.pathComponents
    guard candidateComponents.count > rootComponents.count,
      candidateComponents.prefix(rootComponents.count).elementsEqual(rootComponents)
    else {
      throw FinalCutActionError.invalidPath
    }
    return candidate.path
  }

  private func resolveExistingAncestors(of url: URL) -> URL {
    var ancestor = url
    var missingComponents: [String] = []
    while !FileManager.default.fileExists(atPath: ancestor.path), ancestor.path != "/" {
      missingComponents.insert(ancestor.lastPathComponent, at: 0)
      ancestor.deleteLastPathComponent()
    }
    return missingComponents.reduce(ancestor.resolvingSymlinksInPath()) { resolved, component in
      resolved.appendingPathComponent(component)
    }
    .standardizedFileURL
  }
}

protocol FinalCutActionSystem {
  var sessionRoot: String { get }

  func activeProject(timeout: TimeInterval) throws -> ProjectIdentity?
  func activeProjectMatches(
    _ expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> Bool
  func projectMatchCount(_ identity: ProjectIdentity, timeout: TimeInterval) throws -> Int
  func pressMenu(path: [String], timeout: TimeInterval) throws
  func setExpectedSheetValue(_ value: String, timeout: TimeInterval) throws
  func confirmExpectedSheet(_ confirmation: FinalCutConfirmation, timeout: TimeInterval) throws
  func openDocument(_ path: String, timeout: TimeInterval) throws
  func selectProject(_ identity: ProjectIdentity, timeout: TimeInterval) throws
  func fileSnapshot(_ path: String, timeout: TimeInterval) throws -> ActionFileSnapshot?
  func identityOfExport(
    at path: String, expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> ProjectIdentity?
  func backgroundTasksComplete(timeout: TimeInterval) throws -> Bool
  func blockingDialogs(timeout: TimeInterval) throws -> [BlockingDialog]
  func monotonicTime() -> TimeInterval
  func waitForPoll(maximum: TimeInterval)
}

struct Actions<System: FinalCutActionSystem> {
  private let system: System

  init(system: System) {
    self.system = system
  }

  func duplicateProject(
    expected: ProjectIdentity,
    name: String,
    timeout: TimeInterval
  ) throws -> ProjectIdentity {
    let deadline = try makeDeadline(timeout)
    try validate(expected)
    try requireValidName(name)
    guard name != expected.project else {
      throw FinalCutActionError.invalidName
    }
    try requireActive(expected, deadline: deadline)

    let duplicated = expected.withProject(name)
    let existing = try perform(deadline: deadline) {
      try system.projectMatchCount(duplicated, timeout: $0)
    }
    guard existing == 0 else {
      throw FinalCutActionError.ambiguousProject
    }

    try perform(deadline: deadline) {
      try system.pressMenu(path: FinalCutMenu.duplicate, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.setExpectedSheetValue(name, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.confirmExpectedSheet(.duplicate, timeout: $0)
    }

    return try pollForProject(duplicated, deadline: deadline)
  }

  func exportXML(
    expected: ProjectIdentity,
    output: String,
    timeout: TimeInterval
  ) throws -> ExportReceipt {
    let deadline = try makeDeadline(timeout)
    try validate(expected)
    try requireActive(expected, deadline: deadline)
    let destination = try SessionPath(root: system.sessionRoot).output(output)
    guard URL(fileURLWithPath: destination).pathExtension.lowercased() == "fcpxml" else {
      throw FinalCutActionError.invalidPath
    }
    guard
      try perform(
        deadline: deadline,
        {
          try system.fileSnapshot(destination, timeout: $0)
        }) == nil
    else {
      throw FinalCutActionError.outputAlreadyExists
    }

    try perform(deadline: deadline) {
      try system.pressMenu(path: FinalCutMenu.exportXML, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.setExpectedSheetValue(destination, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.confirmExpectedSheet(.exportXML, timeout: $0)
    }

    let receipt: ExportReceipt = try pollForStableFile(destination, deadline: deadline) {
      guard
        let exported = try perform(
          deadline: deadline,
          {
            try system.identityOfExport(at: destination, expected: expected, timeout: $0)
          })
      else {
        return nil
      }
      guard exported == expected else {
        throw FinalCutActionError.identityMismatch
      }
      try requireActive(expected, deadline: deadline)
      return ExportReceipt(kind: "fcpxml_export", project: expected, output: destination)
    }
    return receipt
  }

  func importXML(
    expected: ProjectIdentity,
    source: String,
    timeout: TimeInterval
  ) throws -> ProjectIdentity {
    let deadline = try makeDeadline(timeout)
    try validate(expected)
    let candidate = try SessionPath(root: system.sessionRoot).input(source)
    let suffix = URL(fileURLWithPath: candidate).pathExtension.lowercased()
    guard suffix == "fcpxml" || suffix == "fcpxmld" else {
      throw FinalCutActionError.invalidPath
    }

    try perform(deadline: deadline) {
      try system.openDocument(candidate, timeout: $0)
    }
    return try pollForProject(expected, deadline: deadline, rejectMissingMedia: true)
  }

  func openProject(
    expected: ProjectIdentity,
    timeout: TimeInterval
  ) throws -> ProjectIdentity {
    let deadline = try makeDeadline(timeout)
    try validate(expected)
    let count = try perform(deadline: deadline) {
      try system.projectMatchCount(expected, timeout: $0)
    }
    guard count > 0 else {
      throw FinalCutActionError.projectNotFound
    }
    guard count == 1 else {
      throw FinalCutActionError.ambiguousProject
    }

    try perform(deadline: deadline) {
      try system.selectProject(expected, timeout: $0)
    }
    return try poll(deadline: deadline) {
      try rejectBlockingDialogs(deadline: deadline)
      return try perform(
        deadline: deadline,
        {
          try system.activeProjectMatches(expected, timeout: $0)
        }) ? expected : nil
    }
  }

  func sharePreview(
    expected: ProjectIdentity,
    output: String,
    timeout: TimeInterval
  ) throws -> ShareReceipt {
    let deadline = try makeDeadline(timeout)
    try validate(expected)
    try requireActive(expected, deadline: deadline)
    let destination = try SessionPath(root: system.sessionRoot).output(output)
    let suffix = URL(fileURLWithPath: destination).pathExtension.lowercased()
    guard suffix == "mov" || suffix == "mp4" else {
      throw FinalCutActionError.invalidPath
    }
    guard
      try perform(
        deadline: deadline,
        {
          try system.fileSnapshot(destination, timeout: $0)
        }) == nil
    else {
      throw FinalCutActionError.outputAlreadyExists
    }

    try perform(deadline: deadline) {
      try system.pressMenu(path: FinalCutMenu.share, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.confirmExpectedSheet(.shareNext, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.setExpectedSheetValue(destination, timeout: $0)
    }
    try perform(deadline: deadline) {
      try system.confirmExpectedSheet(.shareSave, timeout: $0)
    }

    var previous: ActionFileSnapshot?
    return try poll(deadline: deadline) {
      try rejectBlockingDialogs(deadline: deadline)
      let current = try perform(deadline: deadline) {
        try system.fileSnapshot(destination, timeout: $0)
      }
      let backgroundComplete = try perform(deadline: deadline) {
        try system.backgroundTasksComplete(timeout: $0)
      }
      defer { previous = current }
      guard let current, current.size > 0, current == previous, backgroundComplete else {
        return nil
      }
      try requireActive(expected, deadline: deadline)
      return ShareReceipt(kind: "final_cut_share", project: expected, output: destination)
    }
  }

  func inspectDialogs() throws -> [BlockingDialog] {
    try system.blockingDialogs(timeout: 5).map(sanitizedDialog)
  }

  private func pollForProject(
    _ expected: ProjectIdentity,
    deadline: TimeInterval,
    rejectMissingMedia: Bool = false
  ) throws -> ProjectIdentity {
    try poll(deadline: deadline) {
      let dialogs = try perform(deadline: deadline) {
        try system.blockingDialogs(timeout: $0).map(sanitizedDialog)
      }
      if rejectMissingMedia, dialogs.contains(where: isMissingMediaDialog) {
        throw FinalCutActionError.blockingDialog
      }
      guard dialogs.isEmpty else {
        throw FinalCutActionError.blockingDialog
      }
      let count = try perform(deadline: deadline) {
        try system.projectMatchCount(expected, timeout: $0)
      }
      guard count < 2 else {
        throw FinalCutActionError.ambiguousProject
      }
      guard count == 1 else { return nil }
      return try perform(
        deadline: deadline,
        {
          try system.activeProjectMatches(expected, timeout: $0)
        }) ? expected : nil
    }
  }

  private func pollForStableFile<Result>(
    _ path: String,
    deadline: TimeInterval,
    completion: () throws -> Result?
  ) throws -> Result {
    var previous: ActionFileSnapshot?
    return try poll(deadline: deadline) {
      try rejectBlockingDialogs(deadline: deadline)
      let current = try perform(deadline: deadline) {
        try system.fileSnapshot(path, timeout: $0)
      }
      defer { previous = current }
      guard let current, current.size > 0, current == previous else {
        return nil
      }
      return try completion()
    }
  }

  private func poll<Result>(
    deadline: TimeInterval,
    condition: () throws -> Result?
  ) throws -> Result {
    while true {
      _ = try remaining(before: deadline)
      if let result = try condition() {
        _ = try remaining(before: deadline)
        return result
      }
      system.waitForPoll(maximum: try remaining(before: deadline))
      _ = try remaining(before: deadline)
    }
  }

  private func requireActive(_ expected: ProjectIdentity, deadline: TimeInterval) throws {
    guard
      try perform(
        deadline: deadline,
        {
          try system.activeProjectMatches(expected, timeout: $0)
        })
    else {
      throw FinalCutActionError.identityMismatch
    }
  }

  private func rejectBlockingDialogs(deadline: TimeInterval) throws {
    guard
      try perform(
        deadline: deadline,
        {
          try system.blockingDialogs(timeout: $0)
        }
      ).isEmpty
    else {
      throw FinalCutActionError.blockingDialog
    }
  }

  private func makeDeadline(_ timeout: TimeInterval) throws -> TimeInterval {
    try requireTimeout(timeout)
    let now = system.monotonicTime()
    let deadline = now + timeout
    guard deadline.isFinite, deadline > now else {
      throw FinalCutActionError.invalidTimeout
    }
    return deadline
  }

  private func remaining(before deadline: TimeInterval) throws -> TimeInterval {
    let remaining = deadline - system.monotonicTime()
    guard remaining > 0 else {
      throw FinalCutActionError.timedOut
    }
    return remaining
  }

  private func perform<Result>(
    deadline: TimeInterval,
    _ operation: (TimeInterval) throws -> Result
  ) throws -> Result {
    let result = try operation(remaining(before: deadline))
    _ = try remaining(before: deadline)
    return result
  }

  private func validate(_ identity: ProjectIdentity) throws {
    guard !identity.library.isEmpty,
      !identity.event.isEmpty,
      !identity.project.isEmpty,
      identity.duration.isFinite,
      identity.duration >= 0
    else {
      throw FinalCutActionError.invalidIdentity
    }
  }

  private func requireValidName(_ name: String) throws {
    guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
      !name.unicodeScalars.contains(where: CharacterSet.newlines.contains),
      !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
    else {
      throw FinalCutActionError.invalidName
    }
  }

  private func requireTimeout(_ timeout: TimeInterval) throws {
    guard timeout.isFinite, timeout > 0, timeout <= 3_600 else {
      throw FinalCutActionError.invalidTimeout
    }
  }

  private func isMissingMediaDialog(_ dialog: BlockingDialog) -> Bool {
    let title = dialog.title.lowercased()
    return (title.contains("missing") && title.contains("media"))
      || title.contains("relink files")
  }

}

struct FinalCutTimebase: Equatable, Hashable {
  let frameDurationNumerator: Int
  let frameDurationDenominator: Int
  let frameLimit: Int
  let dropFramesPerMinute: Int?

  init?(formatDescription: String) {
    let pattern = #"(?:^|\s)(23\.98|24|25|29\.97|30|50|59\.94|60)[pi](?:\s|$)"#
    guard let expression = try? NSRegularExpression(pattern: pattern),
      let match = expression.firstMatch(
        in: formatDescription,
        range: NSRange(formatDescription.startIndex..., in: formatDescription)
      ),
      let range = Range(match.range(at: 1), in: formatDescription)
    else {
      return nil
    }
    switch String(formatDescription[range]) {
    case "23.98":
      self.init(
        frameDurationNumerator: 1_001, frameDurationDenominator: 24_000,
        frameLimit: 24, dropFramesPerMinute: nil
      )
    case "24":
      self.init(
        frameDurationNumerator: 1, frameDurationDenominator: 24,
        frameLimit: 24, dropFramesPerMinute: nil
      )
    case "25":
      self.init(
        frameDurationNumerator: 1, frameDurationDenominator: 25,
        frameLimit: 25, dropFramesPerMinute: nil
      )
    case "29.97":
      self.init(
        frameDurationNumerator: 1_001, frameDurationDenominator: 30_000,
        frameLimit: 30, dropFramesPerMinute: 2
      )
    case "30":
      self.init(
        frameDurationNumerator: 1, frameDurationDenominator: 30,
        frameLimit: 30, dropFramesPerMinute: nil
      )
    case "50":
      self.init(
        frameDurationNumerator: 1, frameDurationDenominator: 50,
        frameLimit: 50, dropFramesPerMinute: nil
      )
    case "59.94":
      self.init(
        frameDurationNumerator: 1_001, frameDurationDenominator: 60_000,
        frameLimit: 60, dropFramesPerMinute: 4
      )
    case "60":
      self.init(
        frameDurationNumerator: 1, frameDurationDenominator: 60,
        frameLimit: 60, dropFramesPerMinute: nil
      )
    default:
      return nil
    }
  }

  private init(
    frameDurationNumerator: Int, frameDurationDenominator: Int, frameLimit: Int,
    dropFramesPerMinute: Int?
  ) {
    self.frameDurationNumerator = frameDurationNumerator
    self.frameDurationDenominator = frameDurationDenominator
    self.frameLimit = frameLimit
    self.dropFramesPerMinute = dropFramesPerMinute
  }

  var frameDuration: TimeInterval {
    TimeInterval(frameDurationNumerator) / TimeInterval(frameDurationDenominator)
  }
}

struct LiveTimelineStatus: Equatable {
  let project: String
  let hours: Int
  let minutes: Int
  let seconds: Int
  let frames: Int
  let timebase: FinalCutTimebase
  let dropFrame: Bool

  init(
    project: String, hours: Int, minutes: Int, seconds: Int, frames: Int,
    timebase: FinalCutTimebase, dropFrame: Bool = false
  ) {
    self.project = project
    self.hours = hours
    self.minutes = minutes
    self.seconds = seconds
    self.frames = frames
    self.timebase = timebase
    self.dropFrame = dropFrame
  }

  var duration: TimeInterval? {
    guard hours >= 0, (0...59).contains(minutes), (0...59).contains(seconds),
      (0..<timebase.frameLimit).contains(frames)
    else {
      return nil
    }
    let nominalFrames =
      (hours * 3_600 + minutes * 60 + seconds) * timebase.frameLimit + frames
    let droppedFrames: Int
    if dropFrame {
      guard let dropFramesPerMinute = timebase.dropFramesPerMinute else {
        return nil
      }
      let totalMinutes = hours * 60 + minutes
      droppedFrames = dropFramesPerMinute * (totalMinutes - totalMinutes / 10)
    } else {
      droppedFrames = 0
    }
    return TimeInterval(nominalFrames - droppedFrames) * timebase.frameDuration
  }

  func matches(duration expected: TimeInterval) -> Bool {
    guard let duration, expected.isFinite else {
      return false
    }
    return abs(duration - expected) < 0.000_001
  }
}

enum FinalCutAXIdentifier {
  static let activeProjectFormat = "FFViewerProjectFormat"
  static let backgroundTaskProgress = "FFBackgroundTasksProgressIndicator"
  static let shareWindowBackground = "PE Share WindowBackground"
}

enum FinalCutSheetStage {
  case duplicate
  case exportXML
  case shareSettings
  case shareSave

  var buttonTitle: String {
    switch self {
    case .duplicate: "OK"
    case .exportXML, .shareSave: "Save"
    case .shareSettings: "Next..."
    }
  }
}

protocol FinalCutAXElement: AnyObject {
  func accessibilityValue(for attribute: String) -> Any?
  func accessibilityElement(for attribute: String) -> (any FinalCutAXElement)?
  func accessibilityChildren() throws -> [any FinalCutAXElement]
  func accessibilityPress() -> Bool
  func accessibilitySetString(_ value: String, for attribute: String) -> Bool
  func accessibilityAttributeIsSettable(_ attribute: String) -> Bool
  func isSameElement(as other: any FinalCutAXElement) -> Bool
}

private struct ParsedTimelineTimecode {
  let hours: Int
  let minutes: Int
  let seconds: Int
  let frames: Int
  let dropFrame: Bool
}

private struct AccessibilityPathElement {
  let element: any FinalCutAXElement
  let lineage: [any FinalCutAXElement]
}

private final class NativeFinalCutAXElement: FinalCutAXElement {
  let element: AXUIElement

  init(_ element: AXUIElement) {
    self.element = element
  }

  func accessibilityValue(for attribute: String) -> Any? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success else {
      return nil
    }
    return value
  }

  func accessibilityElement(for attribute: String) -> (any FinalCutAXElement)? {
    guard let value = accessibilityValue(for: attribute) as CFTypeRef?,
      CFGetTypeID(value) == AXUIElementGetTypeID()
    else {
      return nil
    }
    let element = unsafeBitCast(value, to: AXUIElement.self)
    return NativeFinalCutAXElement(element)
  }

  func accessibilityChildren() throws -> [any FinalCutAXElement] {
    guard let value = accessibilityValue(for: kAXChildrenAttribute as String) else {
      return []
    }
    guard let children = value as? [AXUIElement] else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return children.map(NativeFinalCutAXElement.init)
  }

  func accessibilityPress() -> Bool {
    AXUIElementPerformAction(element, kAXPressAction as CFString) == .success
  }

  func accessibilitySetString(_ value: String, for attribute: String) -> Bool {
    AXUIElementSetAttributeValue(element, attribute as CFString, value as CFString) == .success
  }

  func accessibilityAttributeIsSettable(_ attribute: String) -> Bool {
    var settable = DarwinBoolean(false)
    return AXUIElementIsAttributeSettable(element, attribute as CFString, &settable) == .success
      && settable.boolValue
  }

  func isSameElement(as other: any FinalCutAXElement) -> Bool {
    guard let other = other as? NativeFinalCutAXElement else { return false }
    return CFEqual(element, other.element)
  }
}

final class LiveFinalCutAX {
  private let root: any FinalCutAXElement
  private let limits = AccessibilityTraversalLimits.default
  private let allowedRoles: Set<String> = [
    kAXApplicationRole as String,
    kAXMenuBarRole as String,
    kAXMenuBarItemRole as String,
    kAXMenuRole as String,
    kAXMenuItemRole as String,
    kAXWindowRole as String,
    kAXSheetRole as String,
    kAXButtonRole as String,
    kAXTextFieldRole as String,
    kAXStaticTextRole as String,
    kAXProgressIndicatorRole as String,
    kAXOutlineRole as String,
    kAXRowRole as String,
    kAXGroupRole as String,
    kAXScrollAreaRole as String,
    "AXBrowser",
    "AXCell",
    "AXDisclosureTriangle",
    "AXLayoutArea",
    "AXList",
    "AXSplitGroup",
    "AXTable",
    "AXToolbar",
    "AXDialog",
  ]

  init(processIdentifier: pid_t) {
    root = NativeFinalCutAXElement(AXUIElementCreateApplication(processIdentifier))
  }

  init(root: any FinalCutAXElement) {
    self.root = root
  }

  func pressMenu(path: [String], timeout: TimeInterval) throws {
    guard !path.isEmpty else {
      throw AccessibilityDiscoveryError.invalidPath
    }
    let deadline = try deadline(after: timeout)
    let menuBar = try pollUntil(deadline: deadline) {
      try uniqueElement(
        titled: nil, role: kAXMenuBarRole as String, beneath: root
      )
    }
    var container = menuBar
    for (index, title) in path.enumerated() {
      let expectedRole = index == 0 ? kAXMenuBarItemRole as String : kAXMenuItemRole as String
      let item = try pollUntil(deadline: deadline) {
        try uniqueElement(titled: title, role: expectedRole, beneath: container)
      }
      try press(item)
      try requireTime(before: deadline)
      guard index < path.count - 1 else { continue }
      container = try pollUntil(deadline: deadline) {
        try uniqueElement(titled: nil, role: kAXMenuRole as String, beneath: item)
      }
    }
  }

  func setUniqueVisibleTextField(
    _ value: String, stage: FinalCutSheetStage, timeout: TimeInterval
  ) throws {
    let deadline = try deadline(after: timeout)
    let container = try expectedContainer(stage: stage, deadline: deadline)
    let fields = try allElements(beneath: container).filter {
      try role(of: $0) == kAXTextFieldRole as String && isVisible($0) && isEnabled($0)
    }
    guard let field = try exactlyOne(fields) else {
      throw AccessibilityDiscoveryError.noMatch
    }
    guard field.accessibilityAttributeIsSettable(kAXValueAttribute as String),
      field.accessibilitySetString(value, for: kAXValueAttribute as String)
    else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    try requireTime(before: deadline)
  }

  func pressUniqueEnabledButton(
    stage: FinalCutSheetStage, timeout: TimeInterval
  ) throws {
    let deadline = try deadline(after: timeout)
    let container = try expectedContainer(stage: stage, deadline: deadline)
    let buttons = try allElements(beneath: container).filter {
      try role(of: $0) == kAXButtonRole as String && title(of: $0) == stage.buttonTitle
        && isVisible($0) && isEnabled($0)
    }
    guard let button = try exactlyOne(buttons) else {
      throw AccessibilityDiscoveryError.noMatch
    }
    try press(button)
    try requireTime(before: deadline)
  }

  func pressProjectRow(_ identity: ProjectIdentity, timeout: TimeInterval) throws {
    let deadline = try deadline(after: timeout)
    var parent = root
    for title in [identity.library, identity.event, identity.project] {
      let row = try pollUntil(deadline: deadline) {
        try uniqueElement(titled: title, role: kAXRowRole as String, beneath: parent)
      }
      try press(row)
      try requireTime(before: deadline)
      parent = row
    }
  }

  func activeTimelineStatus(timeout: TimeInterval) throws -> LiveTimelineStatus? {
    let deadline = try deadline(after: timeout)
    let focusedWindow = try focusedMainWindow()
    let elements = try allElementsWithAncestry(beneath: focusedWindow)
    try requireTime(before: deadline)
    let titleCandidates = elements.compactMap { candidate -> (AccessibilityPathElement, String)? in
      let element = candidate.element
      guard
        let identifier = stringAttribute(kAXIdentifierAttribute as String, of: element)?
          .lowercased(),
        identifier.contains("timeline"),
        identifier.contains("title") || identifier.contains("project"),
        isVisible(element)
      else {
        return nil
      }
      guard let projectTitle = title(of: element) else { return nil }
      return (candidate, projectTitle)
    }
    let durationCandidates = elements.compactMap {
      candidate -> (AccessibilityPathElement, ParsedTimelineTimecode)? in
      let element = candidate.element
      guard
        let identifier = stringAttribute(kAXIdentifierAttribute as String, of: element)?
          .lowercased(),
        identifier.contains("duration"),
        isVisible(element),
        let value = title(of: element)
      else {
        return nil
      }
      guard let timecode = parseTimecode(value) else { return nil }
      return (candidate, timecode)
    }
    let timebases = elements.compactMap {
      candidate -> (AccessibilityPathElement, FinalCutTimebase)? in
      let element = candidate.element
      guard
        stringAttribute(kAXIdentifierAttribute as String, of: element)
          == FinalCutAXIdentifier.activeProjectFormat,
        (try? role(of: element)) == kAXStaticTextRole as String,
        isVisible(element),
        let value = title(of: element)
      else {
        return nil
      }
      guard let timebase = FinalCutTimebase(formatDescription: value) else { return nil }
      return (candidate, timebase)
    }
    let uniqueTitles = Set(titleCandidates.map { $0.1 }.filter { !$0.isEmpty })
    guard !uniqueTitles.isEmpty || !durationCandidates.isEmpty || !timebases.isEmpty else {
      return nil
    }
    guard uniqueTitles.count == 1, durationCandidates.count == 1,
      let project = uniqueTitles.first, let (durationCandidate, timecode) = durationCandidates.first
    else {
      throw AccessibilityDiscoveryError.ambiguousMatch
    }
    let scopedTimebases = timebases.filter { candidate, _ in
      guard
        let scope = candidate.lineage.last(where: { ancestor in
          durationCandidate.lineage.contains { $0.isSameElement(as: ancestor) }
            && titleCandidates.contains { $0.0.lineage.contains { $0.isSameElement(as: ancestor) } }
        })
      else {
        return false
      }
      return !scope.isSameElement(as: focusedWindow)
    }
    guard scopedTimebases.count == 1, let (_, timebase) = scopedTimebases.first else {
      throw AccessibilityDiscoveryError.ambiguousMatch
    }
    return LiveTimelineStatus(
      project: project,
      hours: timecode.hours,
      minutes: timecode.minutes,
      seconds: timecode.seconds,
      frames: timecode.frames,
      timebase: timebase,
      dropFrame: timecode.dropFrame
    )
  }

  func backgroundTasksComplete(timeout: TimeInterval) throws -> Bool {
    let deadline = try deadline(after: timeout)
    let indicators = try allElements(beneath: root).filter {
      try role(of: $0) == kAXProgressIndicatorRole as String
        && stringAttribute(kAXIdentifierAttribute as String, of: $0)
          == FinalCutAXIdentifier.backgroundTaskProgress
        && isVisible($0)
    }
    try requireTime(before: deadline)
    guard indicators.count == 1, let indicator = indicators.first,
      let value = numberAttribute(kAXValueAttribute as String, of: indicator),
      let maximum = numberAttribute(kAXMaxValueAttribute as String, of: indicator),
      value.isFinite, maximum.isFinite, maximum > 0
    else {
      return false
    }
    return value == maximum
  }

  func blockingDialogs(timeout: TimeInterval) throws -> [BlockingDialog] {
    let deadline = try deadline(after: timeout)
    let dialogs = try allElements(beneath: root).compactMap { element -> BlockingDialog? in
      let elementRole = try role(of: element)
      guard elementRole == kAXSheetRole as String || elementRole == "AXDialog",
        isVisible(element)
      else {
        return nil
      }
      return BlockingDialog(role: elementRole, title: title(of: element) ?? "")
    }
    try requireTime(before: deadline)
    return dialogs
  }

  private func expectedContainer(
    stage: FinalCutSheetStage, deadline: TimeInterval
  ) throws -> any FinalCutAXElement {
    try pollUntil(deadline: deadline) {
      let parent: any FinalCutAXElement
      switch stage {
      case .duplicate, .exportXML:
        parent = try focusedMainWindow()
      case .shareSettings:
        parent = root
      case .shareSave:
        parent = try shareSettingsWindow()
      }

      let candidates: [any FinalCutAXElement]
      switch stage {
      case .duplicate:
        candidates = try directChildren(of: parent).filter {
          try role(of: $0) == kAXSheetRole as String
            && stringAttribute(kAXTitleAttribute as String, of: $0) == "Duplicate Project As"
            && isVisible($0)
        }
      case .exportXML:
        candidates = try directChildren(of: parent).filter {
          try role(of: $0) == kAXSheetRole as String
            && stringAttribute(kAXTitleAttribute as String, of: $0) == "Export XML"
            && isVisible($0)
        }
      case .shareSettings:
        candidates = [try shareSettingsWindow()]
      case .shareSave:
        candidates = try directChildren(of: parent).filter {
          try role(of: $0) == kAXSheetRole as String && isVisible($0)
        }
      }
      guard let container = try exactlyOne(candidates) else {
        throw AccessibilityDiscoveryError.noMatch
      }
      let hasExpectedButton = try allElements(beneath: container).contains {
        try role(of: $0) == kAXButtonRole as String && title(of: $0) == stage.buttonTitle
          && isVisible($0) && isEnabled($0)
      }
      guard hasExpectedButton else {
        throw AccessibilityDiscoveryError.noMatch
      }
      return container
    }
  }

  private func focusedMainWindow() throws -> any FinalCutAXElement {
    guard let window = root.accessibilityElement(for: kAXFocusedWindowAttribute as String),
      try role(of: window) == kAXWindowRole as String,
      stringAttribute(kAXSubroleAttribute as String, of: window) == kAXStandardWindowSubrole
        as String,
      boolAttribute(kAXModalAttribute as String, of: window) != true,
      isVisible(window)
    else {
      throw AccessibilityDiscoveryError.noMatch
    }
    return window
  }

  private func shareSettingsWindow() throws -> any FinalCutAXElement {
    let candidates = try directChildren(of: root).filter { element in
      guard try role(of: element) == kAXWindowRole as String,
        stringAttribute(kAXSubroleAttribute as String, of: element) == "AXDialog",
        boolAttribute(kAXModalAttribute as String, of: element) == true,
        isVisible(element)
      else {
        return false
      }
      return try allElements(beneath: element).contains {
        stringAttribute(kAXDescriptionAttribute as String, of: $0)
          == FinalCutAXIdentifier.shareWindowBackground
      }
    }
    guard let window = try exactlyOne(candidates) else {
      throw AccessibilityDiscoveryError.noMatch
    }
    return window
  }

  private func uniqueElement(
    titled title: String?, role expectedRole: String, beneath parent: any FinalCutAXElement
  ) throws -> any FinalCutAXElement {
    let titled = try allElements(beneath: parent).filter {
      title == nil || self.title(of: $0) == title
    }
    let matches = try titled.filter {
      try role(of: $0) == expectedRole && isVisible($0) && isEnabled($0)
    }
    if matches.isEmpty, title != nil, !titled.isEmpty {
      throw AccessibilityDiscoveryError.unexpectedFinalRole
    }
    guard let match = try exactlyOne(matches) else {
      throw AccessibilityDiscoveryError.noMatch
    }
    return match
  }

  private func press(_ element: any FinalCutAXElement) throws {
    guard isVisible(element), isEnabled(element), element.accessibilityPress() else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
  }

  private func allElements(
    beneath element: any FinalCutAXElement
  ) throws -> [any FinalCutAXElement] {
    var elements: [any FinalCutAXElement] = []
    var visited = 0
    try collect(element, depth: 0, visited: &visited, elements: &elements)
    return elements
  }

  private func allElementsWithAncestry(
    beneath element: any FinalCutAXElement
  ) throws -> [AccessibilityPathElement] {
    var elements: [AccessibilityPathElement] = []
    var visited = 0
    try collect(
      element, ancestry: [], depth: 0, visited: &visited, elements: &elements
    )
    return elements
  }

  private func collect(
    _ element: any FinalCutAXElement,
    ancestry: [any FinalCutAXElement],
    depth: Int,
    visited: inout Int,
    elements: inout [AccessibilityPathElement]
  ) throws {
    guard depth <= limits.maxDepth else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    visited += 1
    guard visited <= limits.maxVisitedNodes else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    let elementRole = try role(of: element)
    guard allowedRoles.contains(elementRole) else {
      return
    }
    let lineage = ancestry + [element]
    elements.append(AccessibilityPathElement(element: element, lineage: lineage))
    let children = try directChildren(of: element)
    for child in children {
      try collect(
        child,
        ancestry: lineage,
        depth: depth + 1,
        visited: &visited,
        elements: &elements
      )
    }
  }

  private func collect(
    _ element: any FinalCutAXElement,
    depth: Int,
    visited: inout Int,
    elements: inout [any FinalCutAXElement]
  ) throws {
    guard depth <= limits.maxDepth else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    visited += 1
    guard visited <= limits.maxVisitedNodes else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    let elementRole = try role(of: element)
    guard allowedRoles.contains(elementRole) else {
      return
    }
    elements.append(element)
    let children = try directChildren(of: element)
    for child in children {
      try collect(child, depth: depth + 1, visited: &visited, elements: &elements)
    }
  }

  private func directChildren(
    of element: any FinalCutAXElement
  ) throws -> [any FinalCutAXElement] {
    let children = try element.accessibilityChildren()
    guard children.count <= limits.maxChildrenPerNode else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    return children
  }

  private func role(of element: any FinalCutAXElement) throws -> String {
    guard let value = stringAttribute(kAXRoleAttribute as String, of: element) else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return value
  }

  private func title(of element: any FinalCutAXElement) -> String? {
    for attributeName in [
      kAXTitleAttribute as String,
      kAXValueAttribute as String,
      kAXDescriptionAttribute as String,
    ] {
      if let value = stringAttribute(attributeName, of: element), !value.isEmpty {
        return value
      }
    }
    return nil
  }

  private func isEnabled(_ element: any FinalCutAXElement) -> Bool {
    boolAttribute(kAXEnabledAttribute as String, of: element) ?? false
  }

  private func isVisible(_ element: any FinalCutAXElement) -> Bool {
    !(boolAttribute(kAXHiddenAttribute as String, of: element) ?? false)
  }

  private func stringAttribute(
    _ name: String, of element: any FinalCutAXElement
  ) -> String? {
    element.accessibilityValue(for: name) as? String
  }

  private func boolAttribute(
    _ name: String, of element: any FinalCutAXElement
  ) -> Bool? {
    (element.accessibilityValue(for: name) as? NSNumber)?.boolValue
  }

  private func numberAttribute(
    _ name: String, of element: any FinalCutAXElement
  ) -> Double? {
    (element.accessibilityValue(for: name) as? NSNumber)?.doubleValue
  }

  private func exactlyOne(
    _ elements: [any FinalCutAXElement]
  ) throws -> (any FinalCutAXElement)? {
    let unique = elements.reduce(into: [any FinalCutAXElement]()) { result, element in
      if !result.contains(where: { $0.isSameElement(as: element) }) {
        result.append(element)
      }
    }
    guard unique.count <= 1 else {
      throw AccessibilityDiscoveryError.ambiguousMatch
    }
    return unique.first
  }

  private func deadline(after timeout: TimeInterval) throws -> TimeInterval {
    guard timeout.isFinite, timeout > 0 else {
      throw FinalCutActionError.invalidTimeout
    }
    return ProcessInfo.processInfo.systemUptime + timeout
  }

  private func requireTime(before deadline: TimeInterval) throws {
    guard ProcessInfo.processInfo.systemUptime < deadline else {
      throw FinalCutActionError.timedOut
    }
  }

  private func pollUntil<Result>(
    deadline: TimeInterval, operation: () throws -> Result
  ) throws -> Result {
    while true {
      try requireTime(before: deadline)
      do {
        let result = try operation()
        try requireTime(before: deadline)
        return result
      } catch AccessibilityDiscoveryError.noMatch {
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        guard remaining > 0 else {
          throw FinalCutActionError.timedOut
        }
        Thread.sleep(forTimeInterval: min(0.05, remaining))
      }
    }
  }

  private func parseTimecode(_ value: String) -> ParsedTimelineTimecode? {
    let pattern = #"(\d+):(\d+):(\d+)([:;])(\d+)"#
    guard let expression = try? NSRegularExpression(pattern: pattern),
      let match = expression.matches(
        in: value, range: NSRange(value.startIndex..., in: value)
      ).last,
      match.numberOfRanges == 6
    else {
      return nil
    }
    let numbers = [1, 2, 3, 5].compactMap { index -> Int? in
      guard let range = Range(match.range(at: index), in: value) else { return nil }
      return Int(value[range])
    }
    guard numbers.count == 4 else { return nil }
    guard let separatorRange = Range(match.range(at: 4), in: value) else { return nil }
    return ParsedTimelineTimecode(
      hours: numbers[0],
      minutes: numbers[1],
      seconds: numbers[2],
      frames: numbers[3],
      dropFrame: value[separatorRange] == ";"
    )
  }
}

struct FCPXMLProjectSnapshot {
  let project: String
  let duration: TimeInterval
}

struct FCPXMLProjectReader {
  func read(path: String) throws -> FCPXMLProjectSnapshot? {
    guard let parser = XMLParser(contentsOf: URL(fileURLWithPath: path)) else {
      return nil
    }
    let delegate = FCPXMLProjectParserDelegate()
    parser.delegate = delegate
    parser.shouldResolveExternalEntities = false
    guard parser.parse(), delegate.projectCount == 1,
      let project = delegate.project, let duration = delegate.duration
    else {
      return nil
    }
    return FCPXMLProjectSnapshot(project: project, duration: duration)
  }
}

private final class FCPXMLProjectParserDelegate: NSObject, XMLParserDelegate {
  var projectCount = 0
  var project: String?
  var duration: TimeInterval?
  private var insideProject = false

  func parser(
    _ parser: XMLParser,
    didStartElement elementName: String,
    namespaceURI: String?,
    qualifiedName qName: String?,
    attributes attributeDict: [String: String]
  ) {
    if elementName == "project" {
      projectCount += 1
      insideProject = true
      if projectCount == 1 {
        project = attributeDict["name"]
      }
    } else if elementName == "sequence", insideProject, duration == nil,
      let value = attributeDict["duration"]
    {
      duration = parseRationalTime(value)
    }
  }

  func parser(
    _ parser: XMLParser,
    didEndElement elementName: String,
    namespaceURI: String?,
    qualifiedName qName: String?
  ) {
    if elementName == "project" {
      insideProject = false
    }
  }

  private func parseRationalTime(_ value: String) -> TimeInterval? {
    guard value.hasSuffix("s") else { return nil }
    let raw = String(value.dropLast())
    let pieces = raw.split(separator: "/", omittingEmptySubsequences: false)
    if pieces.count == 1 {
      return Double(pieces[0])
    }
    guard pieces.count == 2, let numerator = Double(pieces[0]),
      let denominator = Double(pieces[1]), denominator != 0
    else {
      return nil
    }
    return numerator / denominator
  }
}

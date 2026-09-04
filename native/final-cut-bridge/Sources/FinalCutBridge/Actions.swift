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

  func activeProject() throws -> ProjectIdentity?
  func projectMatchCount(_ identity: ProjectIdentity) throws -> Int
  func pressMenu(path: [String]) throws
  func setExpectedSheetValue(_ value: String) throws
  func confirmExpectedSheet(_ confirmation: FinalCutConfirmation) throws
  func openDocument(_ path: String) throws
  func selectProject(_ identity: ProjectIdentity) throws
  func fileSnapshot(_ path: String) throws -> ActionFileSnapshot?
  func identityOfExport(at path: String, expected: ProjectIdentity) throws -> ProjectIdentity?
  func backgroundTasksComplete() throws -> Bool
  func blockingDialogs() throws -> [BlockingDialog]
  func monotonicTime() -> TimeInterval
  func waitForPoll()
}

extension FinalCutActionSystem {
  func activeProjectMatches(_ expected: ProjectIdentity) throws -> Bool {
    try activeProject() == expected
  }
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
    try validate(expected)
    try requireValidName(name)
    try requireTimeout(timeout)
    try requireActive(expected)

    try system.pressMenu(path: FinalCutMenu.duplicate)
    try system.setExpectedSheetValue(name)
    try system.confirmExpectedSheet(.duplicate)

    let duplicated = expected.withProject(name)
    return try pollForProject(duplicated, timeout: timeout)
  }

  func exportXML(
    expected: ProjectIdentity,
    output: String,
    timeout: TimeInterval
  ) throws -> ExportReceipt {
    try validate(expected)
    try requireTimeout(timeout)
    try requireActive(expected)
    let destination = try SessionPath(root: system.sessionRoot).output(output)
    guard URL(fileURLWithPath: destination).pathExtension.lowercased() == "fcpxml" else {
      throw FinalCutActionError.invalidPath
    }
    guard try system.fileSnapshot(destination) == nil else {
      throw FinalCutActionError.outputAlreadyExists
    }

    try system.pressMenu(path: FinalCutMenu.exportXML)
    try system.setExpectedSheetValue(destination)
    try system.confirmExpectedSheet(.exportXML)

    let receipt: ExportReceipt = try pollForStableFile(destination, timeout: timeout) {
      guard let exported = try system.identityOfExport(at: destination, expected: expected) else {
        return nil
      }
      guard exported == expected else {
        throw FinalCutActionError.identityMismatch
      }
      try requireActive(expected)
      return ExportReceipt(kind: "fcpxml_export", project: expected, output: destination)
    }
    return receipt
  }

  func importXML(
    expected: ProjectIdentity,
    source: String,
    timeout: TimeInterval
  ) throws -> ProjectIdentity {
    try validate(expected)
    try requireTimeout(timeout)
    let candidate = try SessionPath(root: system.sessionRoot).input(source)
    let suffix = URL(fileURLWithPath: candidate).pathExtension.lowercased()
    guard suffix == "fcpxml" || suffix == "fcpxmld" else {
      throw FinalCutActionError.invalidPath
    }

    try system.openDocument(candidate)
    return try pollForProject(expected, timeout: timeout, rejectMissingMedia: true)
  }

  func openProject(
    expected: ProjectIdentity,
    timeout: TimeInterval
  ) throws -> ProjectIdentity {
    try validate(expected)
    try requireTimeout(timeout)
    let count = try system.projectMatchCount(expected)
    guard count > 0 else {
      throw FinalCutActionError.projectNotFound
    }
    guard count == 1 else {
      throw FinalCutActionError.ambiguousProject
    }

    try system.selectProject(expected)
    return try poll(timeout: timeout) {
      try rejectBlockingDialogs()
      return try system.activeProjectMatches(expected) ? expected : nil
    }
  }

  func sharePreview(
    expected: ProjectIdentity,
    output: String,
    timeout: TimeInterval
  ) throws -> ShareReceipt {
    try validate(expected)
    try requireTimeout(timeout)
    try requireActive(expected)
    let destination = try SessionPath(root: system.sessionRoot).output(output)
    let suffix = URL(fileURLWithPath: destination).pathExtension.lowercased()
    guard suffix == "mov" || suffix == "mp4" else {
      throw FinalCutActionError.invalidPath
    }
    guard try system.fileSnapshot(destination) == nil else {
      throw FinalCutActionError.outputAlreadyExists
    }

    try system.pressMenu(path: FinalCutMenu.share)
    try system.confirmExpectedSheet(.shareNext)
    try system.setExpectedSheetValue(destination)
    try system.confirmExpectedSheet(.shareSave)

    var previous: ActionFileSnapshot?
    return try poll(timeout: timeout) {
      try rejectBlockingDialogs()
      let current = try system.fileSnapshot(destination)
      let backgroundComplete = try system.backgroundTasksComplete()
      defer { previous = current }
      guard let current, current.size > 0, current == previous, backgroundComplete else {
        return nil
      }
      try requireActive(expected)
      return ShareReceipt(kind: "final_cut_share", project: expected, output: destination)
    }
  }

  func inspectDialogs() throws -> [BlockingDialog] {
    try system.blockingDialogs().map(sanitizedDialog)
  }

  private func pollForProject(
    _ expected: ProjectIdentity,
    timeout: TimeInterval,
    rejectMissingMedia: Bool = false
  ) throws -> ProjectIdentity {
    try poll(timeout: timeout) {
      let dialogs = try inspectDialogs()
      if rejectMissingMedia, dialogs.contains(where: isMissingMediaDialog) {
        throw FinalCutActionError.blockingDialog
      }
      guard dialogs.isEmpty else {
        throw FinalCutActionError.blockingDialog
      }
      let count = try system.projectMatchCount(expected)
      guard count < 2 else {
        throw FinalCutActionError.ambiguousProject
      }
      guard count == 1 else { return nil }
      return try system.activeProjectMatches(expected) ? expected : nil
    }
  }

  private func pollForStableFile<Result>(
    _ path: String,
    timeout: TimeInterval,
    completion: () throws -> Result?
  ) throws -> Result {
    var previous: ActionFileSnapshot?
    return try poll(timeout: timeout) {
      try rejectBlockingDialogs()
      let current = try system.fileSnapshot(path)
      defer { previous = current }
      guard let current, current.size > 0, current == previous else {
        return nil
      }
      return try completion()
    }
  }

  private func poll<Result>(
    timeout: TimeInterval,
    condition: () throws -> Result?
  ) throws -> Result {
    let deadline = system.monotonicTime() + timeout
    while true {
      if let result = try condition() {
        return result
      }
      guard system.monotonicTime() < deadline else {
        throw FinalCutActionError.timedOut
      }
      system.waitForPoll()
    }
  }

  private func requireActive(_ expected: ProjectIdentity) throws {
    guard try system.activeProjectMatches(expected) else {
      throw FinalCutActionError.identityMismatch
    }
  }

  private func rejectBlockingDialogs() throws {
    guard try system.blockingDialogs().isEmpty else {
      throw FinalCutActionError.blockingDialog
    }
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

struct LiveTimelineStatus: Equatable {
  let project: String
  let hours: Int
  let minutes: Int
  let seconds: Int
  let frames: Int

  var duration: TimeInterval {
    TimeInterval(hours * 3_600 + minutes * 60 + seconds) + TimeInterval(frames) / 25
  }

  func matches(duration expected: TimeInterval) -> Bool {
    guard hours >= 0, (0...59).contains(minutes), (0...59).contains(seconds), frames >= 0,
      expected.isFinite
    else {
      return false
    }
    let wholeSeconds = TimeInterval(hours * 3_600 + minutes * 60 + seconds)
    let formats: [(duration: TimeInterval, frameLimit: Int)] = [
      (1 / 24, 24),
      (1 / 25, 25),
      (1 / 30, 30),
      (1 / 50, 50),
      (1 / 60, 60),
      (1_001 / 24_000, 24),
      (1_001 / 30_000, 30),
      (1_001 / 60_000, 60),
    ]
    return formats.contains { format in
      frames < format.frameLimit
        && abs(wholeSeconds + TimeInterval(frames) * format.duration - expected) < 0.000_001
    }
  }
}

final class LiveFinalCutAX {
  private let root: AXUIElement
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
    root = AXUIElementCreateApplication(processIdentifier)
  }

  func pressMenu(path: [String]) throws {
    guard !path.isEmpty else {
      throw AccessibilityDiscoveryError.invalidPath
    }
    var roots = [root]
    for (index, title) in path.enumerated() {
      var visited = 0
      var matches: [AXUIElement] = []
      for searchRoot in roots {
        try collectFirstMatches(
          titled: title,
          beneath: searchRoot,
          depth: 0,
          visited: &visited,
          matches: &matches
        )
      }
      matches = unique(matches)
      guard matches.count == 1, let match = matches.first else {
        throw matches.isEmpty
          ? AccessibilityDiscoveryError.noMatch : AccessibilityDiscoveryError.ambiguousMatch
      }
      let expectedRole = index == path.count - 1 ? kAXMenuItemRole as String : nil
      if let expectedRole, try role(of: match) != expectedRole {
        throw AccessibilityDiscoveryError.unexpectedFinalRole
      }
      try press(match)
      Thread.sleep(forTimeInterval: 0.05)
      roots = try children(of: match)
      guard roots.count <= limits.maxChildrenPerNode else {
        throw AccessibilityDiscoveryError.traversalLimitExceeded
      }
    }
  }

  func setUniqueVisibleTextField(_ value: String, expectedButton: String) throws {
    let containers = try expectedContainers(buttonTitle: expectedButton)
    guard containers.count == 1, let container = containers.first else {
      throw containers.isEmpty
        ? AccessibilityDiscoveryError.noMatch : AccessibilityDiscoveryError.ambiguousMatch
    }
    let fields = try allElements(beneath: container).filter {
      try role(of: $0) == kAXTextFieldRole as String && isVisible($0) && isEnabled($0)
    }
    let uniqueFields = unique(fields)
    guard uniqueFields.count == 1, let field = uniqueFields.first else {
      throw uniqueFields.isEmpty
        ? AccessibilityDiscoveryError.noMatch : AccessibilityDiscoveryError.ambiguousMatch
    }

    var settable = DarwinBoolean(false)
    let settableStatus = AXUIElementIsAttributeSettable(
      field, kAXValueAttribute as CFString, &settable
    )
    guard settableStatus == .success, settable.boolValue else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    guard
      AXUIElementSetAttributeValue(
        field, kAXValueAttribute as CFString, value as CFString
      ) == .success
    else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
  }

  func pressUniqueEnabledButton(title: String) throws {
    let containers = try expectedContainers(buttonTitle: title)
    let buttons = try containers.flatMap { container in
      try allElements(beneath: container).filter {
        try role(of: $0) == kAXButtonRole as String && self.title(of: $0) == title
          && isVisible($0) && isEnabled($0)
      }
    }
    let uniqueButtons = unique(buttons)
    guard uniqueButtons.count == 1, let button = uniqueButtons.first else {
      throw uniqueButtons.isEmpty
        ? AccessibilityDiscoveryError.noMatch : AccessibilityDiscoveryError.ambiguousMatch
    }
    try press(button)
    Thread.sleep(forTimeInterval: 0.05)
  }

  func pressProjectRow(_ identity: ProjectIdentity) throws {
    try pressUniqueEnabledRow(title: identity.library)
    try pressUniqueEnabledRow(title: identity.event)
    try pressUniqueEnabledRow(title: identity.project)
  }

  func activeTimelineStatus() throws -> LiveTimelineStatus? {
    let elements = try allElements(beneath: root)
    let titleCandidates = elements.compactMap { element -> String? in
      guard
        let identifier = stringAttribute(kAXIdentifierAttribute as String, of: element)?
          .lowercased(),
        identifier.contains("timeline"),
        identifier.contains("title") || identifier.contains("project"),
        isVisible(element)
      else {
        return nil
      }
      return title(of: element)
    }
    let durationCandidates = elements.compactMap { element -> String? in
      guard
        let identifier = stringAttribute(kAXIdentifierAttribute as String, of: element)?
          .lowercased(),
        identifier.contains("duration"),
        isVisible(element)
      else {
        return nil
      }
      return title(of: element)
    }
    let uniqueTitles = Array(Set(titleCandidates.filter { !$0.isEmpty }))
    let durations = durationCandidates.compactMap(parseTimecode)
    guard !uniqueTitles.isEmpty || !durations.isEmpty else {
      return nil
    }
    guard uniqueTitles.count == 1, durations.count == 1,
      let project = uniqueTitles.first, let timecode = durations.first
    else {
      throw AccessibilityDiscoveryError.ambiguousMatch
    }
    return LiveTimelineStatus(
      project: project,
      hours: timecode[0],
      minutes: timecode[1],
      seconds: timecode[2],
      frames: timecode[3]
    )
  }

  func backgroundTasksComplete() throws -> Bool {
    let indicators = try allElements(beneath: root).filter {
      try role(of: $0) == kAXProgressIndicatorRole as String && isVisible($0)
    }
    for indicator in indicators {
      guard let value = numberAttribute(kAXValueAttribute as String, of: indicator) else {
        return false
      }
      let maximum = numberAttribute(kAXMaxValueAttribute as String, of: indicator) ?? 1
      if value < maximum {
        return false
      }
    }
    return true
  }

  func blockingDialogs() throws -> [BlockingDialog] {
    try allElements(beneath: root).compactMap { element in
      let elementRole = try role(of: element)
      guard elementRole == kAXSheetRole as String || elementRole == "AXDialog",
        isVisible(element)
      else {
        return nil
      }
      return BlockingDialog(role: elementRole, title: title(of: element) ?? "")
    }
  }

  private func expectedContainers(buttonTitle: String) throws -> [AXUIElement] {
    let containers = try allElements(beneath: root).filter {
      let elementRole = try role(of: $0)
      return
        (elementRole == kAXSheetRole as String || elementRole == "AXDialog"
        || elementRole == kAXWindowRole as String) && isVisible($0)
    }
    let matching = try containers.filter { container in
      try allElements(beneath: container).contains { element in
        (try? role(of: element)) == kAXButtonRole as String && title(of: element) == buttonTitle
          && isVisible(element) && isEnabled(element)
      }
    }
    let sheets = matching.filter {
      (try? role(of: $0)) == kAXSheetRole as String || (try? role(of: $0)) == "AXDialog"
    }
    return unique(sheets.isEmpty ? matching : sheets)
  }

  private func pressUniqueEnabledRow(title: String) throws {
    let rows = try allElements(beneath: root).filter {
      try role(of: $0) == kAXRowRole as String && self.title(of: $0) == title
        && isVisible($0) && isEnabled($0)
    }
    let uniqueRows = unique(rows)
    guard uniqueRows.count == 1, let row = uniqueRows.first else {
      throw uniqueRows.isEmpty
        ? AccessibilityDiscoveryError.noMatch : AccessibilityDiscoveryError.ambiguousMatch
    }
    try press(row)
    Thread.sleep(forTimeInterval: 0.05)
  }

  private func press(_ element: AXUIElement) throws {
    guard isVisible(element), isEnabled(element),
      AXUIElementPerformAction(element, kAXPressAction as CFString) == .success
    else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
  }

  private func allElements(beneath element: AXUIElement) throws -> [AXUIElement] {
    var elements: [AXUIElement] = []
    var visited = 0
    try collect(element, depth: 0, visited: &visited, elements: &elements)
    return elements
  }

  private func collect(
    _ element: AXUIElement,
    depth: Int,
    visited: inout Int,
    elements: inout [AXUIElement]
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
    let elementChildren = try children(of: element)
    guard elementChildren.count <= limits.maxChildrenPerNode else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    for child in elementChildren {
      try collect(child, depth: depth + 1, visited: &visited, elements: &elements)
    }
  }

  private func collectFirstMatches(
    titled title: String,
    beneath element: AXUIElement,
    depth: Int,
    visited: inout Int,
    matches: inout [AXUIElement]
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
    if self.title(of: element) == title {
      matches.append(element)
      return
    }
    let elementChildren = try children(of: element)
    guard elementChildren.count <= limits.maxChildrenPerNode else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    for child in elementChildren {
      try collectFirstMatches(
        titled: title,
        beneath: child,
        depth: depth + 1,
        visited: &visited,
        matches: &matches
      )
    }
  }

  private func role(of element: AXUIElement) throws -> String {
    guard let value = stringAttribute(kAXRoleAttribute as String, of: element) else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return value
  }

  private func title(of element: AXUIElement) -> String? {
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

  private func children(of element: AXUIElement) throws -> [AXUIElement] {
    guard let value = attribute(kAXChildrenAttribute as String, of: element) else {
      return []
    }
    guard let children = value as? [AXUIElement] else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return children
  }

  private func isEnabled(_ element: AXUIElement) -> Bool {
    boolAttribute(kAXEnabledAttribute as String, of: element) ?? false
  }

  private func isVisible(_ element: AXUIElement) -> Bool {
    !(boolAttribute(kAXHiddenAttribute as String, of: element) ?? false)
  }

  private func stringAttribute(_ name: String, of element: AXUIElement) -> String? {
    attribute(name, of: element) as? String
  }

  private func boolAttribute(_ name: String, of element: AXUIElement) -> Bool? {
    (attribute(name, of: element) as? NSNumber)?.boolValue
  }

  private func numberAttribute(_ name: String, of element: AXUIElement) -> Double? {
    (attribute(name, of: element) as? NSNumber)?.doubleValue
  }

  private func attribute(_ name: String, of element: AXUIElement) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
      return nil
    }
    return value
  }

  private func unique(_ elements: [AXUIElement]) -> [AXUIElement] {
    elements.reduce(into: []) { result, element in
      if !result.contains(where: { CFEqual($0, element) }) {
        result.append(element)
      }
    }
  }

  private func parseTimecode(_ value: String) -> [Int]? {
    let pattern = #"(\d+):(\d+):(\d+)[:;](\d+)"#
    guard let expression = try? NSRegularExpression(pattern: pattern),
      let match = expression.matches(
        in: value, range: NSRange(value.startIndex..., in: value)
      ).last,
      match.numberOfRanges == 5
    else {
      return nil
    }
    let numbers = (1..<5).compactMap { index -> Int? in
      guard let range = Range(match.range(at: index), in: value) else { return nil }
      return Int(value[range])
    }
    guard numbers.count == 4 else { return nil }
    return numbers
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

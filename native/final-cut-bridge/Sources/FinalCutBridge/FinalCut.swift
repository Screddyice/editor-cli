import AppKit
import Carbon
import Foundation

struct FinalCutApplication: Equatable {
  let bundleIdentifier: String?
  let version: String?
  let processIdentifier: pid_t
}

protocol FinalCutSystem {
  func runningApplications(bundleIdentifier: String) -> [FinalCutApplication]
  func isAccessibilityTrusted() -> Bool
  func accessibilityRoot(processIdentifier: pid_t) -> any AccessibilityNode
  func readLibraryNames(processIdentifier: pid_t) throws -> [String]
}

extension FinalCutSystem {
  func accessibilityRoot(processIdentifier: pid_t) -> any AccessibilityNode {
    LiveAccessibilityNode.application(processIdentifier: processIdentifier)
  }
}

struct FinalCutProbeResult: Equatable {
  let ready: Bool
  let bundleIdentifier: String
  let version: String
  let accessibilityTrusted: Bool
  let automationAuthorized: Bool
  let libraryNames: [String]
  let activeProject: ProjectIdentity?
  let blockingDialogs: [BlockingDialog]
}

enum FinalCutProbeError: Error, Equatable {
  case unexpectedProcessCount
  case wrongBundleIdentifier
  case unsupportedVersion
}

enum FinalCutAutomationError: Error, Equatable {
  case notAuthorized
  case invalidTarget
  case eventFailed
  case invalidReply
}

protocol FinalCutAutomationTransport {
  func readLibraryNames(
    processIdentifier: pid_t,
    eventClass: AEEventClass,
    eventID: AEEventID,
    askUserIfNeeded: Bool,
    sendOptions: NSAppleEventDescriptor.SendOptions
  ) throws -> [String]
}

struct FinalCutAutomationReader<Transport: FinalCutAutomationTransport> {
  let transport: Transport

  func readLibraryNames(processIdentifier: pid_t) throws -> [String] {
    let baseOptions = UInt(kAEWaitReply | kAENeverInteract)
    let noPromptOptions = baseOptions | UInt(kAEDoNotPromptForUserConsent)
    return try transport.readLibraryNames(
      processIdentifier: processIdentifier,
      eventClass: AEEventClass(kAECoreSuite),
      eventID: AEEventID(kAEGetData),
      askUserIfNeeded: false,
      sendOptions: NSAppleEventDescriptor.SendOptions(rawValue: noPromptOptions)
    )
  }
}

struct FinalCutProbe<System: FinalCutSystem> {
  static var bundleIdentifier: String { "com.apple.FinalCutApp" }
  static var supportedVersion: String { "12.3" }

  let system: System

  func run() throws -> FinalCutProbeResult {
    let applications = system.runningApplications(bundleIdentifier: Self.bundleIdentifier)
    guard applications.count == 1, let application = applications.first else {
      throw FinalCutProbeError.unexpectedProcessCount
    }
    guard application.bundleIdentifier == Self.bundleIdentifier else {
      throw FinalCutProbeError.wrongBundleIdentifier
    }
    guard application.version == Self.supportedVersion else {
      throw FinalCutProbeError.unsupportedVersion
    }

    let accessibilityTrusted = system.isAccessibilityTrusted()
    if accessibilityTrusted {
      _ = system.accessibilityRoot(processIdentifier: application.processIdentifier)
    }

    let libraryNames: [String]
    let automationAuthorized: Bool
    do {
      libraryNames = try system.readLibraryNames(
        processIdentifier: application.processIdentifier
      )
      automationAuthorized = true
    } catch {
      libraryNames = []
      automationAuthorized = false
    }

    var activeProject: ProjectIdentity?
    var blockingDialogs: [BlockingDialog] = []
    if let actionSystem = system as? any FinalCutActionSystem {
      if accessibilityTrusted && automationAuthorized {
        activeProject = try? actionSystem.activeProject()
      }
      if accessibilityTrusted {
        blockingDialogs = (try? actionSystem.blockingDialogs().map(sanitizedDialog)) ?? []
      }
    }

    return FinalCutProbeResult(
      ready: accessibilityTrusted && automationAuthorized,
      bundleIdentifier: Self.bundleIdentifier,
      version: Self.supportedVersion,
      accessibilityTrusted: accessibilityTrusted,
      automationAuthorized: automationAuthorized,
      libraryNames: libraryNames,
      activeProject: activeProject,
      blockingDialogs: blockingDialogs
    )
  }
}

final class LiveFinalCutSystem: FinalCutSystem, FinalCutActionSystem {
  let sessionRoot: String
  private var expectedSheet: ExpectedSheet?

  init(sessionRoot: String = "/tmp/editor-cli-probe") {
    self.sessionRoot = sessionRoot
  }

  func runningApplications(bundleIdentifier: String) -> [FinalCutApplication] {
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier).map { app in
      let version =
        app.bundleURL
        .flatMap(Bundle.init(url:))?
        .object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
      return FinalCutApplication(
        bundleIdentifier: app.bundleIdentifier,
        version: version,
        processIdentifier: app.processIdentifier
      )
    }
  }

  func isAccessibilityTrusted() -> Bool {
    AXIsProcessTrusted()
  }

  func accessibilityRoot(processIdentifier: pid_t) -> any AccessibilityNode {
    LiveAccessibilityNode.application(processIdentifier: processIdentifier)
  }

  func readLibraryNames(processIdentifier: pid_t) throws -> [String] {
    try FinalCutAutomationReader(transport: NativeFinalCutAutomationTransport())
      .readLibraryNames(processIdentifier: processIdentifier)
  }

  func activeProject() throws -> ProjectIdentity? {
    let processIdentifier = try verifiedActionProcessIdentifier()
    let status = try LiveFinalCutAX(processIdentifier: processIdentifier).activeTimelineStatus()
    guard let status else {
      return nil
    }
    let matches = try NativeFinalCutProjectReader().locations(processIdentifier: processIdentifier)
      .filter { $0.project == status.project }
    guard matches.count <= 1 else {
      throw FinalCutActionError.ambiguousProject
    }
    guard let match = matches.first else {
      return nil
    }
    return ProjectIdentity(
      library: match.library,
      event: match.event,
      project: match.project,
      duration: status.duration
    )
  }

  func projectMatchCount(_ identity: ProjectIdentity) throws -> Int {
    let processIdentifier = try verifiedActionProcessIdentifier()
    return try NativeFinalCutProjectReader().locations(processIdentifier: processIdentifier)
      .filter {
        $0.library == identity.library && $0.event == identity.event
          && $0.project == identity.project
      }
      .count
  }

  func activeProjectMatches(_ expected: ProjectIdentity) throws -> Bool {
    let processIdentifier = try verifiedActionProcessIdentifier()
    guard
      let status = try LiveFinalCutAX(processIdentifier: processIdentifier)
        .activeTimelineStatus(),
      status.project == expected.project,
      status.matches(duration: expected.duration)
    else {
      return false
    }
    let matches = try NativeFinalCutProjectReader().locations(processIdentifier: processIdentifier)
      .filter {
        $0.library == expected.library && $0.event == expected.event
          && $0.project == expected.project
      }
    return matches.count == 1
  }

  func pressMenu(path: [String]) throws {
    let allowed = [FinalCutMenu.duplicate, FinalCutMenu.exportXML, FinalCutMenu.share]
    guard allowed.contains(path) else {
      throw AccessibilityDiscoveryError.invalidPath
    }
    try actionAccessibility(requireAutomation: true).pressMenu(path: path)
    if path == FinalCutMenu.duplicate {
      expectedSheet = .duplicate
    } else if path == FinalCutMenu.exportXML {
      expectedSheet = .exportXML
    } else {
      expectedSheet = .shareSettings
    }
  }

  func setExpectedSheetValue(_ value: String) throws {
    let button: String
    switch expectedSheet {
    case .duplicate:
      button = "OK"
    case .exportXML, .shareSave:
      button = "Save"
    default:
      throw AccessibilityDiscoveryError.noMatch
    }
    try actionAccessibility(requireAutomation: true).setUniqueVisibleTextField(
      value, expectedButton: button
    )
  }

  func confirmExpectedSheet(_ confirmation: FinalCutConfirmation) throws {
    let button: String
    switch (expectedSheet, confirmation) {
    case (.duplicate, .duplicate):
      button = "OK"
      expectedSheet = nil
    case (.exportXML, .exportXML):
      button = "Save"
      expectedSheet = nil
    case (.shareSettings, .shareNext):
      button = "Next..."
      expectedSheet = .shareSave
    case (.shareSave, .shareSave):
      button = "Save"
      expectedSheet = nil
    default:
      throw AccessibilityDiscoveryError.noMatch
    }
    try actionAccessibility(requireAutomation: true).pressUniqueEnabledButton(title: button)
  }

  func openDocument(_ path: String) throws {
    _ = try verifiedActionProcessIdentifier()
    guard NSWorkspace.shared.open(URL(fileURLWithPath: path)) else {
      throw FinalCutActionError.projectNotFound
    }
  }

  func selectProject(_ identity: ProjectIdentity) throws {
    guard try projectMatchCount(identity) == 1 else {
      throw FinalCutActionError.projectNotFound
    }
    try actionAccessibility(requireAutomation: true).pressProjectRow(identity)
  }

  func fileSnapshot(_ path: String) throws -> ActionFileSnapshot? {
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: path),
      let fileType = attributes[.type] as? FileAttributeType,
      fileType == .typeRegular,
      let size = attributes[.size] as? NSNumber,
      let modified = attributes[.modificationDate] as? Date
    else {
      return nil
    }
    return ActionFileSnapshot(size: size.uint64Value, modifiedAt: modified.timeIntervalSince1970)
  }

  func identityOfExport(
    at path: String, expected: ProjectIdentity
  ) throws -> ProjectIdentity? {
    guard let exported = try FCPXMLProjectReader().read(path: path) else {
      return nil
    }
    return ProjectIdentity(
      library: expected.library,
      event: expected.event,
      project: exported.project,
      duration: exported.duration
    )
  }

  func backgroundTasksComplete() throws -> Bool {
    try actionAccessibility(requireAutomation: true).backgroundTasksComplete()
  }

  func blockingDialogs() throws -> [BlockingDialog] {
    try actionAccessibility(requireAutomation: false).blockingDialogs()
  }

  func monotonicTime() -> TimeInterval {
    ProcessInfo.processInfo.systemUptime
  }

  func waitForPoll() {
    Thread.sleep(forTimeInterval: 0.1)
  }

  private func verifiedProcessIdentifier() throws -> pid_t {
    let applications = runningApplications(bundleIdentifier: FinalCutProbe<Self>.bundleIdentifier)
    guard applications.count == 1, let application = applications.first else {
      throw FinalCutProbeError.unexpectedProcessCount
    }
    guard application.bundleIdentifier == FinalCutProbe<Self>.bundleIdentifier else {
      throw FinalCutProbeError.wrongBundleIdentifier
    }
    guard application.version == FinalCutProbe<Self>.supportedVersion else {
      throw FinalCutProbeError.unsupportedVersion
    }
    return application.processIdentifier
  }

  private func verifiedActionProcessIdentifier() throws -> pid_t {
    let processIdentifier = try verifiedProcessIdentifier()
    guard isAccessibilityTrusted() else {
      throw FinalCutActionError.accessibilityNotTrusted
    }
    _ = try readLibraryNames(processIdentifier: processIdentifier)
    return processIdentifier
  }

  private func actionAccessibility(requireAutomation: Bool) throws -> LiveFinalCutAX {
    let processIdentifier =
      try requireAutomation ? verifiedActionProcessIdentifier() : verifiedProcessIdentifier()
    guard isAccessibilityTrusted() else {
      throw FinalCutActionError.accessibilityNotTrusted
    }
    return LiveFinalCutAX(processIdentifier: processIdentifier)
  }
}

private enum ExpectedSheet {
  case duplicate
  case exportXML
  case shareSettings
  case shareSave
}

private struct FinalCutProjectLocation: Equatable {
  let library: String
  let event: String
  let project: String
}

private struct NativeFinalCutProjectReader {
  func locations(processIdentifier: pid_t) throws -> [FinalCutProjectLocation] {
    guard
      NSRunningApplication(processIdentifier: processIdentifier)?.bundleIdentifier
        == FinalCutProbe<LiveFinalCutSystem>.bundleIdentifier
    else {
      throw FinalCutProbeError.wrongBundleIdentifier
    }

    let script = """
      set fieldSep to (ASCII character 31)
      set recordSep to (ASCII character 30)
      set output to ""
      tell application id "com.apple.FinalCutApp"
        repeat with libraryItem in libraries
          set libraryName to name of libraryItem
          repeat with eventItem in (events of libraryItem)
            set eventName to name of eventItem
            repeat with projectItem in (projects of eventItem)
              set output to output & libraryName & fieldSep & eventName & fieldSep & (name of projectItem) & recordSep
            end repeat
          end repeat
        end repeat
      end tell
      return output
      """
    let completion = DispatchSemaphore(value: 0)
    let result = FinalCutProjectReadResult()
    DispatchQueue.global(qos: .userInitiated).async {
      guard let appleScript = NSAppleScript(source: script) else {
        result.store(.failure(.invalidReply))
        completion.signal()
        return
      }
      var error: NSDictionary?
      let reply = appleScript.executeAndReturnError(&error)
      guard error == nil, let output = reply.stringValue else {
        result.store(.failure(.eventFailed))
        completion.signal()
        return
      }
      result.store(.success(output))
      completion.signal()
    }
    guard completion.wait(timeout: .now() + 5) == .success else {
      throw FinalCutAutomationError.eventFailed
    }
    let output = try result.load().get()
    return output.split(separator: Character("\u{001e}"), omittingEmptySubsequences: true)
      .map { record in
        record.split(separator: Character("\u{001f}"), omittingEmptySubsequences: false)
      }
      .compactMap { fields in
        guard fields.count == 3 else { return nil }
        return FinalCutProjectLocation(
          library: String(fields[0]), event: String(fields[1]), project: String(fields[2])
        )
      }
  }
}

private final class FinalCutProjectReadResult: @unchecked Sendable {
  private let lock = NSLock()
  private var result: Result<String, FinalCutAutomationError>?

  func store(_ value: Result<String, FinalCutAutomationError>) {
    lock.lock()
    result = value
    lock.unlock()
  }

  func load() throws -> Result<String, FinalCutAutomationError> {
    lock.lock()
    defer { lock.unlock() }
    guard let result else {
      throw FinalCutAutomationError.invalidReply
    }
    return result
  }
}

struct NativeFinalCutAutomationTransport: FinalCutAutomationTransport {
  func readLibraryNames(
    processIdentifier: pid_t,
    eventClass: AEEventClass,
    eventID: AEEventID,
    askUserIfNeeded: Bool,
    sendOptions: NSAppleEventDescriptor.SendOptions
  ) throws -> [String] {
    let target = NSAppleEventDescriptor(processIdentifier: processIdentifier)
    guard let targetDescription = target.aeDesc else {
      throw FinalCutAutomationError.invalidTarget
    }

    let permission = AEDeterminePermissionToAutomateTarget(
      targetDescription,
      eventClass,
      eventID,
      askUserIfNeeded
    )
    guard permission == noErr else {
      throw FinalCutAutomationError.notAuthorized
    }

    let event = NSAppleEventDescriptor(
      eventClass: eventClass,
      eventID: eventID,
      targetDescriptor: target,
      returnID: AEReturnID(kAutoGenerateReturnID),
      transactionID: AETransactionID(kAnyTransactionID)
    )
    event.setParam(libraryNameSpecifier(), forKeyword: AEKeyword(keyDirectObject))

    let reply: NSAppleEventDescriptor
    do {
      reply = try event.sendEvent(options: sendOptions, timeout: 5)
    } catch {
      throw FinalCutAutomationError.eventFailed
    }

    if let errorNumber = reply.paramDescriptor(forKeyword: AEKeyword(keyErrorNumber)),
      errorNumber.int32Value != 0
    {
      throw FinalCutAutomationError.eventFailed
    }
    guard let result = reply.paramDescriptor(forKeyword: AEKeyword(keyDirectObject)) else {
      throw FinalCutAutomationError.invalidReply
    }
    return try strings(from: result)
  }

  private func libraryNameSpecifier() -> NSAppleEventDescriptor {
    let allLibraries = objectSpecifier(
      desiredClass: fourCharacterCode("fxlb"),
      keyForm: OSType(formAbsolutePosition),
      keyData: NSAppleEventDescriptor(enumCode: OSType(kAEAll)),
      container: .null()
    )
    return objectSpecifier(
      desiredClass: OSType(typeProperty),
      keyForm: OSType(formPropertyID),
      keyData: NSAppleEventDescriptor(typeCode: fourCharacterCode("pnam")),
      container: allLibraries
    )
  }

  private func objectSpecifier(
    desiredClass: OSType,
    keyForm: OSType,
    keyData: NSAppleEventDescriptor,
    container: NSAppleEventDescriptor
  ) -> NSAppleEventDescriptor {
    let record = NSAppleEventDescriptor.record()
    record.setDescriptor(
      NSAppleEventDescriptor(typeCode: desiredClass),
      forKeyword: AEKeyword(keyAEDesiredClass)
    )
    record.setDescriptor(
      NSAppleEventDescriptor(enumCode: keyForm),
      forKeyword: AEKeyword(keyAEKeyForm)
    )
    record.setDescriptor(keyData, forKeyword: AEKeyword(keyAEKeyData))
    record.setDescriptor(container, forKeyword: AEKeyword(keyAEContainer))
    return record.coerce(toDescriptorType: DescType(typeObjectSpecifier)) ?? record
  }

  private func strings(from descriptor: NSAppleEventDescriptor) throws -> [String] {
    if descriptor.numberOfItems == 0 {
      if let value = descriptor.stringValue {
        return [value]
      }
      if descriptor.descriptorType == DescType(typeAEList) {
        return []
      }
      throw FinalCutAutomationError.invalidReply
    }

    return try (1...descriptor.numberOfItems).map { index in
      guard let value = descriptor.atIndex(index)?.stringValue else {
        throw FinalCutAutomationError.invalidReply
      }
      return value
    }
  }

  private func fourCharacterCode(_ value: String) -> OSType {
    value.utf8.reduce(0) { result, byte in
      (result << 8) | OSType(byte)
    }
  }
}

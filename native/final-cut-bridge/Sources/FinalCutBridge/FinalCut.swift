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

protocol TimedFinalCutAutomationTransport: FinalCutAutomationTransport {
  func readLibraryNames(
    processIdentifier: pid_t,
    eventClass: AEEventClass,
    eventID: AEEventID,
    askUserIfNeeded: Bool,
    sendOptions: NSAppleEventDescriptor.SendOptions,
    timeout: TimeInterval
  ) throws -> [String]
}

struct FinalCutAutomationReader<Transport: FinalCutAutomationTransport> {
  let transport: Transport

  func readLibraryNames(processIdentifier: pid_t) throws -> [String] {
    try readLibraryNames(processIdentifier: processIdentifier, timeout: 5)
  }

  func readLibraryNames(
    processIdentifier: pid_t, timeout: TimeInterval
  ) throws -> [String] {
    let baseOptions = UInt(kAEWaitReply | kAENeverInteract)
    let noPromptOptions = baseOptions | UInt(kAEDoNotPromptForUserConsent)
    if let timedTransport = transport as? any TimedFinalCutAutomationTransport {
      return try timedTransport.readLibraryNames(
        processIdentifier: processIdentifier,
        eventClass: AEEventClass(kAECoreSuite),
        eventID: AEEventID(kAEGetData),
        askUserIfNeeded: false,
        sendOptions: NSAppleEventDescriptor.SendOptions(rawValue: noPromptOptions),
        timeout: timeout
      )
    }
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
        activeProject = try? actionSystem.activeProject(timeout: 5)
      }
      if accessibilityTrusted {
        blockingDialogs =
          (try? actionSystem.blockingDialogs(timeout: 5).map(sanitizedDialog)) ?? []
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
  private var expectedSheet: FinalCutSheetStage?

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

  func activeProject(timeout: TimeInterval) throws -> ProjectIdentity? {
    let deadline = try actionDeadline(timeout)
    let processIdentifier = try verifiedActionProcessIdentifier(
      timeout: remaining(before: deadline)
    )
    let status = try LiveFinalCutAX(processIdentifier: processIdentifier).activeTimelineStatus(
      timeout: remaining(before: deadline)
    )
    guard let status else {
      return nil
    }
    let locations = try NativeFinalCutProjectReader().locations(
      processIdentifier: processIdentifier, timeout: remaining(before: deadline)
    )
    let identity = try ActiveProjectResolver.resolve(status: status, locations: locations)
    _ = try remaining(before: deadline)
    return identity
  }

  func projectMatchCount(
    _ identity: ProjectIdentity, timeout: TimeInterval
  ) throws -> Int {
    let deadline = try actionDeadline(timeout)
    let processIdentifier = try verifiedActionProcessIdentifier(
      timeout: remaining(before: deadline)
    )
    let count = try NativeFinalCutProjectReader().locations(
      processIdentifier: processIdentifier, timeout: remaining(before: deadline)
    )
    .filter {
      $0.library == identity.library && $0.event == identity.event
        && $0.project == identity.project
    }
    .count
    _ = try remaining(before: deadline)
    return count
  }

  func activeProjectMatches(
    _ expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> Bool {
    try activeProject(timeout: timeout) == expected
  }

  func pressMenu(path: [String], timeout: TimeInterval) throws {
    let allowed = [FinalCutMenu.duplicate, FinalCutMenu.exportXML, FinalCutMenu.share]
    guard allowed.contains(path) else {
      throw AccessibilityDiscoveryError.invalidPath
    }
    let deadline = try actionDeadline(timeout)
    let accessibility = try actionAccessibility(
      requireAutomation: true, timeout: remaining(before: deadline)
    )
    try accessibility.pressMenu(path: path, timeout: remaining(before: deadline))
    _ = try remaining(before: deadline)
    if path == FinalCutMenu.duplicate {
      expectedSheet = .duplicate
    } else if path == FinalCutMenu.exportXML {
      expectedSheet = .exportXML
    } else {
      expectedSheet = .shareSettings
    }
  }

  func setExpectedSheetValue(_ value: String, timeout: TimeInterval) throws {
    guard let stage = expectedSheet else {
      throw AccessibilityDiscoveryError.noMatch
    }
    switch stage {
    case .duplicate, .exportXML, .shareSave:
      break
    case .shareSettings:
      throw AccessibilityDiscoveryError.noMatch
    }
    let deadline = try actionDeadline(timeout)
    let accessibility = try actionAccessibility(
      requireAutomation: true, timeout: remaining(before: deadline)
    )
    try accessibility.setUniqueVisibleTextField(
      value, stage: stage, timeout: remaining(before: deadline)
    )
    _ = try remaining(before: deadline)
  }

  func confirmExpectedSheet(
    _ confirmation: FinalCutConfirmation, timeout: TimeInterval
  ) throws {
    let stage: FinalCutSheetStage
    switch (expectedSheet, confirmation) {
    case (.duplicate, .duplicate):
      stage = .duplicate
      expectedSheet = nil
    case (.exportXML, .exportXML):
      stage = .exportXML
      expectedSheet = nil
    case (.shareSettings, .shareNext):
      stage = .shareSettings
      expectedSheet = .shareSave
    case (.shareSave, .shareSave):
      stage = .shareSave
      expectedSheet = nil
    default:
      throw AccessibilityDiscoveryError.noMatch
    }
    let deadline = try actionDeadline(timeout)
    let accessibility = try actionAccessibility(
      requireAutomation: true, timeout: remaining(before: deadline)
    )
    try accessibility.pressUniqueEnabledButton(
      stage: stage, timeout: remaining(before: deadline)
    )
    _ = try remaining(before: deadline)
  }

  func openDocument(_ path: String, timeout: TimeInterval) throws {
    let deadline = try actionDeadline(timeout)
    _ = try verifiedActionProcessIdentifier(timeout: remaining(before: deadline))
    guard NSWorkspace.shared.open(URL(fileURLWithPath: path)) else {
      throw FinalCutActionError.projectNotFound
    }
    _ = try remaining(before: deadline)
  }

  func selectProject(_ identity: ProjectIdentity, timeout: TimeInterval) throws {
    let deadline = try actionDeadline(timeout)
    guard try projectMatchCount(identity, timeout: remaining(before: deadline)) == 1 else {
      throw FinalCutActionError.projectNotFound
    }
    let accessibility = try actionAccessibility(
      requireAutomation: true, timeout: remaining(before: deadline)
    )
    try accessibility.pressProjectRow(identity, timeout: remaining(before: deadline))
    _ = try remaining(before: deadline)
  }

  func fileSnapshot(_ path: String, timeout: TimeInterval) throws -> ActionFileSnapshot? {
    let deadline = try actionDeadline(timeout)
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: path),
      let fileType = attributes[.type] as? FileAttributeType,
      fileType == .typeRegular,
      let size = attributes[.size] as? NSNumber,
      let modified = attributes[.modificationDate] as? Date
    else {
      _ = try remaining(before: deadline)
      return nil
    }
    _ = try remaining(before: deadline)
    return ActionFileSnapshot(size: size.uint64Value, modifiedAt: modified.timeIntervalSince1970)
  }

  func identityOfExport(
    at path: String, expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> ProjectIdentity? {
    let deadline = try actionDeadline(timeout)
    guard let exported = try FCPXMLProjectReader().read(path: path) else {
      _ = try remaining(before: deadline)
      return nil
    }
    _ = try remaining(before: deadline)
    return ProjectIdentity(
      library: expected.library,
      event: expected.event,
      project: exported.project,
      duration: exported.duration
    )
  }

  func backgroundTasksComplete(timeout: TimeInterval) throws -> Bool {
    let deadline = try actionDeadline(timeout)
    let accessibility = try actionAccessibility(
      requireAutomation: true, timeout: remaining(before: deadline)
    )
    let complete = try accessibility.backgroundTasksComplete(
      timeout: remaining(before: deadline)
    )
    _ = try remaining(before: deadline)
    return complete
  }

  func blockingDialogs(timeout: TimeInterval) throws -> [BlockingDialog] {
    let deadline = try actionDeadline(timeout)
    let accessibility = try actionAccessibility(
      requireAutomation: false, timeout: remaining(before: deadline)
    )
    let dialogs = try accessibility.blockingDialogs(timeout: remaining(before: deadline))
    _ = try remaining(before: deadline)
    return dialogs
  }

  func monotonicTime() -> TimeInterval {
    ProcessInfo.processInfo.systemUptime
  }

  func waitForPoll(maximum: TimeInterval) {
    Thread.sleep(forTimeInterval: min(0.1, maximum))
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

  private func verifiedActionProcessIdentifier(timeout: TimeInterval) throws -> pid_t {
    let deadline = try actionDeadline(timeout)
    let processIdentifier = try verifiedProcessIdentifier()
    guard isAccessibilityTrusted() else {
      throw FinalCutActionError.accessibilityNotTrusted
    }
    _ = try FinalCutAutomationReader(transport: NativeFinalCutAutomationTransport())
      .readLibraryNames(
        processIdentifier: processIdentifier,
        timeout: min(5, remaining(before: deadline))
      )
    _ = try remaining(before: deadline)
    return processIdentifier
  }

  private func actionAccessibility(
    requireAutomation: Bool, timeout: TimeInterval
  ) throws -> LiveFinalCutAX {
    let deadline = try actionDeadline(timeout)
    let processIdentifier =
      try requireAutomation
      ? verifiedActionProcessIdentifier(timeout: remaining(before: deadline))
      : verifiedProcessIdentifier()
    guard isAccessibilityTrusted() else {
      throw FinalCutActionError.accessibilityNotTrusted
    }
    _ = try remaining(before: deadline)
    return LiveFinalCutAX(processIdentifier: processIdentifier)
  }

  private func actionDeadline(_ timeout: TimeInterval) throws -> TimeInterval {
    guard timeout.isFinite, timeout > 0 else {
      throw FinalCutActionError.invalidTimeout
    }
    let now = monotonicTime()
    let deadline = now + timeout
    guard deadline.isFinite, deadline > now else {
      throw FinalCutActionError.invalidTimeout
    }
    return deadline
  }

  private func remaining(before deadline: TimeInterval) throws -> TimeInterval {
    let remaining = deadline - monotonicTime()
    guard remaining > 0 else {
      throw FinalCutActionError.timedOut
    }
    return remaining
  }
}

struct FinalCutProjectLocation: Equatable {
  let library: String
  let event: String
  let project: String
}

enum ActiveProjectResolver {
  static func resolve(
    status: LiveTimelineStatus, locations: [FinalCutProjectLocation]
  ) throws -> ProjectIdentity? {
    let namedLocations = locations.filter { $0.project == status.project }
    guard namedLocations.count <= 1 else {
      throw FinalCutActionError.ambiguousProject
    }
    guard let location = namedLocations.first, let duration = status.duration else {
      return nil
    }
    return ProjectIdentity(
      library: location.library,
      event: location.event,
      project: location.project,
      duration: duration
    )
  }
}

private struct NativeFinalCutProjectReader {
  func locations(
    processIdentifier: pid_t, timeout: TimeInterval
  ) throws -> [FinalCutProjectLocation] {
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
    guard timeout.isFinite, timeout > 0,
      completion.wait(timeout: .now() + min(5, timeout)) == .success
    else {
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

struct NativeFinalCutAutomationTransport: TimedFinalCutAutomationTransport {
  func readLibraryNames(
    processIdentifier: pid_t,
    eventClass: AEEventClass,
    eventID: AEEventID,
    askUserIfNeeded: Bool,
    sendOptions: NSAppleEventDescriptor.SendOptions
  ) throws -> [String] {
    try readLibraryNames(
      processIdentifier: processIdentifier,
      eventClass: eventClass,
      eventID: eventID,
      askUserIfNeeded: askUserIfNeeded,
      sendOptions: sendOptions,
      timeout: 5
    )
  }

  func readLibraryNames(
    processIdentifier: pid_t,
    eventClass: AEEventClass,
    eventID: AEEventID,
    askUserIfNeeded: Bool,
    sendOptions: NSAppleEventDescriptor.SendOptions,
    timeout: TimeInterval
  ) throws -> [String] {
    guard timeout.isFinite, timeout > 0 else {
      throw FinalCutAutomationError.eventFailed
    }
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
      reply = try event.sendEvent(options: sendOptions, timeout: min(5, timeout))
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

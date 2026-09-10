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

enum FinalCutProbeError: Error, Equatable, LocalizedError {
  case unexpectedProcessCount
  case wrongBundleIdentifier
  case unsupportedVersion

  var errorDescription: String? {
    switch self {
    case .unexpectedProcessCount: "Open Final Cut Pro 12.3 with one running application instance."
    case .wrongBundleIdentifier:
      "Final Cut application bundle identity does not match com.apple.FinalCutApp."
    case .unsupportedVersion: "Final Cut Pro 12.3 is required for native control."
    }
  }
}

enum FinalCutAutomationError: Error, Equatable, LocalizedError {
  case notAuthorized
  case invalidTarget
  case eventFailed
  case invalidReply

  var errorDescription: String? {
    switch self {
    case .notAuthorized:
      "Automation permission for Final Cut is missing; run editor-cli permissions request."
    case .invalidTarget: "Final Cut Automation target is invalid."
    case .eventFailed: "Final Cut library inspection failed or timed out."
    case .invalidReply: "Final Cut returned an invalid library inspection response."
    }
  }
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

protocol FinalCutPermissionTransport {
  func accessibilityTrusted(prompt: Bool) -> Bool
  func automationAuthorized(processIdentifier: pid_t, prompt: Bool) -> Bool
}

struct FinalCutPermissionRequestResult: Equatable {
  let accessibilityTrusted: Bool
  let automationAuthorized: Bool
}

struct FinalCutPermissionRequester<
  System: FinalCutSystem, Permissions: FinalCutPermissionTransport
> {
  let system: System
  let permissions: Permissions

  func run() throws -> FinalCutPermissionRequestResult {
    let applications = system.runningApplications(
      bundleIdentifier: FinalCutProbe<System>.bundleIdentifier
    )
    guard applications.count == 1, let application = applications.first else {
      throw FinalCutProbeError.unexpectedProcessCount
    }
    guard application.bundleIdentifier == FinalCutProbe<System>.bundleIdentifier else {
      throw FinalCutProbeError.wrongBundleIdentifier
    }
    guard application.version == FinalCutProbe<System>.supportedVersion else {
      throw FinalCutProbeError.unsupportedVersion
    }
    return FinalCutPermissionRequestResult(
      accessibilityTrusted: permissions.accessibilityTrusted(prompt: true),
      automationAuthorized: permissions.automationAuthorized(
        processIdentifier: application.processIdentifier, prompt: true
      )
    )
  }
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
    } catch FinalCutAutomationError.notAuthorized {
      libraryNames = []
      automationAuthorized = false
    }

    var blockingDialogs: [BlockingDialog] = []
    if let actionSystem = system as? any FinalCutActionSystem {
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
      activeProject: nil,
      blockingDialogs: blockingDialogs
    )
  }
}

final class LiveFinalCutSystem: FinalCutSystem, FinalCutActionSystem {
  let sessionRoot: String
  private var expectedSheet: FinalCutSheetStage?
  private let foreground: FinalCutForeground

  init(
    sessionRoot: String = "/tmp/editor-cli-probe",
    foreground: FinalCutForeground = .live
  ) {
    self.sessionRoot = sessionRoot
    self.foreground = foreground
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
    try FinalCutInspectionCoordinator(
      verify: { try self.verifiedActionProcessIdentifier(timeout: $0) },
      reveal: { processIdentifier, remaining in
        try LiveFinalCutAX(processIdentifier: processIdentifier)
          .revealActiveProjectLocation(timeout: remaining)
      },
      readRecords: { processIdentifier, location, remaining in
        try TargetedFinalCutProjectReader().records(
          processIdentifier: processIdentifier, library: location.library, event: location.event,
          timeout: remaining
        )
      },
      readStatus: { processIdentifier, remaining in
        try LiveFinalCutAX(processIdentifier: processIdentifier).activeTimelineStatus(
          timeout: remaining)
      },
      clock: { self.monotonicTime() }
    ).inspect(timeout: timeout)
  }

  func projectMatchCount(
    _ identity: ProjectIdentity, timeout: TimeInterval
  ) throws -> Int {
    let deadline = try actionDeadline(timeout)
    let processIdentifier = try verifiedActionProcessIdentifier(
      timeout: remaining(before: deadline)
    )
    let count = try TargetedFinalCutProjectReader().records(
      processIdentifier: processIdentifier, library: identity.library, event: identity.event,
      timeout: remaining(before: deadline)
    )
    .filter {
      $0.library == identity.library && $0.event == identity.event
        && $0.project == identity.project
    }
    .count
    _ = try remaining(before: deadline)
    return count
  }

  func selectedProjectMatches(
    _ expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> Bool {
    let deadline = try actionDeadline(timeout)
    // Reading the browser needs the window tree, and the window tree needs the
    // foreground raise that only the automation path performs. Same as
    // selectProject, which made the selection this is checking.
    let accessibility = try actionAccessibility(
      requireAutomation: true, timeout: remaining(before: deadline)
    )
    let matches = try accessibility.selectedProjectMatches(
      expected.project, timeout: remaining(before: deadline)
    )
    _ = try remaining(before: deadline)
    return matches
  }

  func activeProjectMatches(
    _ expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> Bool {
    guard let active = try activeProject(timeout: timeout) else { return false }
    return active.library == expected.library && active.event == expected.event
      && active.project == expected.project && abs(active.duration - expected.duration) < 0.000_001
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

  func dismissTransientUI() {
    expectedSheet = nil
    guard isAccessibilityTrusted(), let processIdentifier = try? verifiedProcessIdentifier()
    else { return }
    LiveFinalCutAX(processIdentifier: processIdentifier).dismissTransientUI()
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
    // A save panel needs its folder chosen before its name, and it only takes
    // a name. A duplicate sheet takes a bare project name and no folder.
    var name = value
    if value.hasPrefix("/") {
      let url = URL(fileURLWithPath: value)
      try accessibility.selectSaveDirectory(
        url.deletingLastPathComponent().path, stage: stage,
        timeout: remaining(before: deadline)
      )
      name = url.lastPathComponent
    }
    try accessibility.setUniqueVisibleTextField(
      name, stage: stage, timeout: remaining(before: deadline)
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
    guard let attributes = try? FileManager.default.attributesOfItem(
      atPath: FinalCutExportArtifact.readablePath(of: path)
    ),
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
    guard
      let exported = try FCPXMLProjectReader().read(
        path: FinalCutExportArtifact.readablePath(of: path)
      )
    else {
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

  func openLibraryNames(timeout: TimeInterval) throws -> [String] {
    let deadline = try actionDeadline(timeout)
    let processIdentifier = try verifiedActionProcessIdentifier(
      timeout: remaining(before: deadline)
    )
    let names = try FinalCutAutomationReader(transport: NativeFinalCutAutomationTransport())
      .readLibraryNames(
        processIdentifier: processIdentifier, timeout: remaining(before: deadline)
      )
    _ = try remaining(before: deadline)
    return names
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
    try foreground.raise(processIdentifier: processIdentifier, deadline: deadline)
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
    try foreground.raise(processIdentifier: processIdentifier, deadline: deadline)
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

struct FinalCutInspectionError: LocalizedError {
  let stage: String
  let underlying: Error
  var errorDescription: String? {
    "Final Cut \(stage): \((underlying as? LocalizedError)?.errorDescription ?? String(describing: underlying))"
  }
}

struct FinalCutInspectionCoordinator {
  let verify: (TimeInterval) throws -> pid_t
  let reveal: (pid_t, TimeInterval) throws -> FinalCutProjectLocation?
  let readRecords: (pid_t, FinalCutProjectLocation, TimeInterval) throws -> [NativeProjectRecord]
  let readStatus: (pid_t, TimeInterval) throws -> LiveTimelineStatus?
  let clock: () -> TimeInterval

  func inspect(timeout: TimeInterval) throws -> ProjectIdentity? {
    var stage = "initial verification"
    do {
      let started = clock()
      let deadline = started + timeout
      guard timeout.isFinite, timeout > 0, timeout <= 3600,
        started.isFinite, deadline.isFinite, deadline > started
      else { throw FinalCutActionError.invalidTimeout }
      func remaining() throws -> TimeInterval {
        let value = deadline - clock()
        guard value.isFinite, value > 0 else { throw FinalCutActionError.timedOut }
        return value
      }
      func timed<Value>(_ operation: (TimeInterval) throws -> Value) throws -> Value {
        let value = try operation(remaining())
        _ = try remaining()
        return value
      }
      let processIdentifier = try timed(verify)
      stage = "active-project reveal"
      guard let location = try timed({ try reveal(processIdentifier, $0) }) else { return nil }
      stage = "scoped project metadata"
      let records = try timed({ try readRecords(processIdentifier, location, $0) })
        .filter { $0.project == location.project }
      guard records.count == 1, let record = records.first else {
        throw FinalCutActionError.ambiguousProject
      }
      guard record.library == location.library, record.event == location.event else {
        throw FinalCutActionError.identityMismatch
      }
      stage = "active-project scope revalidation"
      guard try timed({ try reveal(processIdentifier, $0) }) == location else {
        throw FinalCutActionError.identityMismatch
      }
      stage = "timeline metadata comparison"
      guard let status = try timed({ try readStatus(processIdentifier, $0) }),
        status.project == record.project, status.matches(duration: record.duration),
        abs(status.timebase.frameDuration - record.frameDuration) < 0.000_001
      else { throw FinalCutActionError.identityMismatch }
      let identity = try ActiveProjectResolver.resolve(status: status, locations: [location])
      _ = try remaining()
      return identity
    } catch {
      throw FinalCutInspectionError(stage: stage, underlying: error)
    }
  }
}

enum ActiveProjectResolver {
  static func resolve(
    status: LiveTimelineStatus, locations: [FinalCutProjectLocation]
  ) throws -> ProjectIdentity? {
    guard let location = try location(status: status, locations: locations),
      let duration = status.duration
    else {
      return nil
    }
    return ProjectIdentity(
      library: location.library,
      event: location.event,
      project: location.project,
      duration: duration
    )
  }

  static func matches(
    status: LiveTimelineStatus,
    locations: [FinalCutProjectLocation],
    expected: ProjectIdentity
  ) throws -> Bool {
    guard let location = try location(status: status, locations: locations) else {
      return false
    }
    return matches(status: status, location: location, expected: expected)
  }

  static func matches(
    status: LiveTimelineStatus,
    location: FinalCutProjectLocation,
    expected: ProjectIdentity
  ) -> Bool {
    location.library == expected.library
      && location.event == expected.event
      && location.project == expected.project
      && status.project == expected.project
      && status.matches(duration: expected.duration)
  }

  private static func location(
    status: LiveTimelineStatus, locations: [FinalCutProjectLocation]
  ) throws -> FinalCutProjectLocation? {
    let namedLocations = locations.filter { $0.project == status.project }
    guard namedLocations.count <= 1 else {
      throw FinalCutActionError.ambiguousProject
    }
    return namedLocations.first
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
    event.setParam(try libraryNameSpecifier(), forKeyword: AEKeyword(keyDirectObject))

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

  func libraryNameSpecifier() throws -> NSAppleEventDescriptor {
    // An absolute-position object selector requires typeAbsoluteOrdinal, not
    // typeEnumerated. Apple Events consume the OSType in host byte order.
    var all = OSType(kAEAll)
    guard
      let ordinal = NSAppleEventDescriptor(
        descriptorType: DescType(typeAbsoluteOrdinal), bytes: &all,
        length: MemoryLayout<OSType>.size
      )
    else { throw FinalCutAutomationError.invalidTarget }
    let allLibraries = objectSpecifier(
      desiredClass: fourCharacterCode("fxlb"),
      keyForm: OSType(formAbsolutePosition),
      keyData: ordinal,
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

struct LiveFinalCutPermissionTransport: FinalCutPermissionTransport {
  func accessibilityTrusted(prompt: Bool) -> Bool {
    let option = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
    let options = [option: prompt] as CFDictionary
    return AXIsProcessTrustedWithOptions(options)
  }

  func automationAuthorized(processIdentifier: pid_t, prompt: Bool) -> Bool {
    let target = NSAppleEventDescriptor(processIdentifier: processIdentifier)
    guard let targetDescription = target.aeDesc else {
      return false
    }
    return AEDeterminePermissionToAutomateTarget(
      targetDescription,
      AEEventClass(kAECoreSuite),
      AEEventID(kAEGetData),
      prompt
    ) == noErr
  }
}

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
  let accessibilityTrusted: Bool
  let automationAuthorized: Bool
  let libraryNames: [String]
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

    return FinalCutProbeResult(
      ready: accessibilityTrusted && automationAuthorized,
      accessibilityTrusted: accessibilityTrusted,
      automationAuthorized: automationAuthorized,
      libraryNames: libraryNames
    )
  }
}

struct LiveFinalCutSystem: FinalCutSystem {
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

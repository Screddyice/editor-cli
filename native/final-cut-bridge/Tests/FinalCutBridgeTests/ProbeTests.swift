import AppKit
import Carbon
import Foundation
import XCTest

@testable import FinalCutBridge

final class ProbeTests: XCTestCase {
  func testProbeRejectsWrongBundleIdentifier() throws {
    let app = FakeFinalCut(bundleID: "example.fake", version: "12.3")

    XCTAssertThrowsError(try FinalCutProbe(system: app).run()) { error in
      XCTAssertEqual(error as? FinalCutProbeError, .wrongBundleIdentifier)
    }
  }

  func testProbeRequiresExactVersionAndPermissions() throws {
    let app = FakeFinalCut(
      bundleID: "com.apple.FinalCutApp",
      version: "12.3",
      axTrusted: true,
      automation: true
    )

    let result = try FinalCutProbe(system: app).run()

    XCTAssertTrue(result.ready)
    XCTAssertTrue(result.accessibilityTrusted)
    XCTAssertTrue(result.automationAuthorized)
    XCTAssertEqual(result.libraryNames, ["Canary Library"])
  }

  func testProbeRejectsAnyOtherVersion() throws {
    let app = FakeFinalCut(bundleID: "com.apple.FinalCutApp", version: "12.3.1")

    XCTAssertThrowsError(try FinalCutProbe(system: app).run()) { error in
      XCTAssertEqual(error as? FinalCutProbeError, .unsupportedVersion)
    }
  }

  func testProbeRequiresExactlyOneRunningProcess() throws {
    let app = FakeFinalCut(
      applications: [
        .init(bundleIdentifier: "com.apple.FinalCutApp", version: "12.3", processIdentifier: 101),
        .init(bundleIdentifier: "com.apple.FinalCutApp", version: "12.3", processIdentifier: 102),
      ]
    )

    XCTAssertThrowsError(try FinalCutProbe(system: app).run()) { error in
      XCTAssertEqual(error as? FinalCutProbeError, .unexpectedProcessCount)
    }
  }

  func testProbeReportsUntrustedAccessibilityWhileTestingAutomation() throws {
    let app = FakeFinalCut(
      bundleID: "com.apple.FinalCutApp",
      version: "12.3",
      axTrusted: false,
      automation: true
    )

    let result = try FinalCutProbe(system: app).run()

    XCTAssertFalse(result.ready)
    XCTAssertFalse(result.accessibilityTrusted)
    XCTAssertTrue(result.automationAuthorized)
  }

  func testProbeReportsAutomationDenialWithoutThrowing() throws {
    let app = FakeFinalCut(
      bundleID: "com.apple.FinalCutApp",
      version: "12.3",
      axTrusted: true,
      automation: false
    )

    let result = try FinalCutProbe(system: app).run()

    XCTAssertFalse(result.ready)
    XCTAssertTrue(result.accessibilityTrusted)
    XCTAssertFalse(result.automationAuthorized)
    XCTAssertEqual(result.libraryNames, [])
  }

  func testAutomationReaderUsesNonPromptingReadOnlyEventPolicy() throws {
    let transport = RecordingAutomationTransport()

    let names = try FinalCutAutomationReader(transport: transport).readLibraryNames(
      processIdentifier: 100
    )

    XCTAssertEqual(names, ["Canary Library"])
    XCTAssertEqual(transport.eventClass, AEEventClass(kAECoreSuite))
    XCTAssertEqual(transport.eventID, AEEventID(kAEGetData))
    XCTAssertEqual(transport.askUserIfNeeded, false)
    XCTAssertEqual(
      (transport.sendOptions?.rawValue ?? 0) & UInt(kAEDoNotPromptForUserConsent),
      UInt(kAEDoNotPromptForUserConsent)
    )
    XCTAssertEqual(
      (transport.sendOptions?.rawValue ?? 0) & UInt(kAENeverInteract),
      UInt(kAENeverInteract)
    )
  }
}

struct FakeFinalCut: FinalCutSystem {
  let applications: [FinalCutApplication]
  let axTrusted: Bool
  let automation: Bool

  init(
    bundleID: String,
    version: String,
    axTrusted: Bool = true,
    automation: Bool = true
  ) {
    self.init(
      applications: [
        .init(bundleIdentifier: bundleID, version: version, processIdentifier: 100)
      ],
      axTrusted: axTrusted,
      automation: automation
    )
  }

  init(applications: [FinalCutApplication], axTrusted: Bool = true, automation: Bool = true) {
    self.applications = applications
    self.axTrusted = axTrusted
    self.automation = automation
  }

  func runningApplications(bundleIdentifier: String) -> [FinalCutApplication] {
    applications
  }

  func isAccessibilityTrusted() -> Bool {
    axTrusted
  }

  func readLibraryNames(processIdentifier: pid_t) throws -> [String] {
    guard automation else {
      throw FinalCutAutomationError.notAuthorized
    }
    return ["Canary Library"]
  }
}

final class RecordingAutomationTransport: FinalCutAutomationTransport {
  var eventClass: AEEventClass?
  var eventID: AEEventID?
  var askUserIfNeeded: Bool?
  var sendOptions: NSAppleEventDescriptor.SendOptions?

  func readLibraryNames(
    processIdentifier: pid_t,
    eventClass: AEEventClass,
    eventID: AEEventID,
    askUserIfNeeded: Bool,
    sendOptions: NSAppleEventDescriptor.SendOptions
  ) throws -> [String] {
    self.eventClass = eventClass
    self.eventID = eventID
    self.askUserIfNeeded = askUserIfNeeded
    self.sendOptions = sendOptions
    return ["Canary Library"]
  }
}

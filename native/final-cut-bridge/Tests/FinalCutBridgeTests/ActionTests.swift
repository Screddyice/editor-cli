import Foundation
import XCTest

@testable import FinalCutBridge

final class ActionTests: XCTestCase {
  func testActionPayloadRejectsUnknownKeys() throws {
    let data =
      #"{"protocolVersion":1,"action":"open_project","sessionRoot":"/tmp/session","payload":{"expected":{"library":"Canary","event":"Event","project":"Pass 1","duration_seconds":8},"timeout":2,"script":"id"}}"#
      .data(using: .utf8)!
    let request = try StrictProtocol.decodeRequest(data)

    XCTAssertThrowsError(try ActionPayload.decode(request))
  }

  func testActionPayloadDecodesExactProjectIdentity() throws {
    let data =
      #"{"protocolVersion":1,"action":"open_project","sessionRoot":"/tmp/session","payload":{"expected":{"library":"Canary","event":"Event","project":"Pass 1","duration_seconds":8},"timeout":2}}"#
      .data(using: .utf8)!
    let request = try StrictProtocol.decodeRequest(data)

    guard case .openProject(let expected, let timeout) = try ActionPayload.decode(request) else {
      return XCTFail("Expected open_project payload")
    }
    XCTAssertEqual(expected, .canaryCandidate)
    XCTAssertEqual(timeout, 2)
  }

  func testExecutableRejectsUnknownActionPayloadBeforeFinalCutAccess() throws {
    let executable = Bundle(for: Self.self).bundleURL
      .deletingLastPathComponent()
      .appendingPathComponent("FinalCutBridge")
    let process = Process()
    let input = Pipe()
    let output = Pipe()
    process.executableURL = executable
    process.standardInput = input
    process.standardOutput = output
    process.standardError = Pipe()
    try process.run()
    let request =
      #"{"protocolVersion":1,"action":"open_project","sessionRoot":"/tmp/session","payload":{"expected":{"library":"Canary","event":"Event","project":"Pass 1","duration_seconds":8},"timeout":2,"script":"id"}}"#
    input.fileHandleForWriting.write(Data(request.utf8))
    try input.fileHandleForWriting.close()
    process.waitUntilExit()

    let response = try XCTUnwrap(
      try JSONSerialization.jsonObject(
        with: output.fileHandleForReading.readDataToEndOfFile()
      ) as? [String: Any]
    )
    XCTAssertEqual(response["ok"] as? Bool, false)
    XCTAssertEqual(process.terminationStatus, 1)
  }

  func testDeniedProbeDoesNotAttemptSupplementalProjectQuery() throws {
    let system = FakeActionSystem(active: nil)
    system.automationAuthorized = false

    let result = try FinalCutProbe(system: system).run()

    XCTAssertFalse(result.automationAuthorized)
    XCTAssertEqual(system.activeProjectReads, 0)
    XCTAssertEqual(system.dialogReads, 1)
  }

  func testDuplicateUsesExactGeneratedNameAndPollsIdentity() throws {
    let source = ProjectIdentity(library: "Canary", event: "Event", project: "Source", duration: 8)
    let system = FakeActionSystem(active: source)

    let result = try Actions(system: system).duplicateProject(
      expected: source, name: "Source - a1b2c3 - Before AI", timeout: 2
    )

    XCTAssertEqual(result.project, "Source - a1b2c3 - Before AI")
    XCTAssertEqual(system.menuPaths, [FinalCutMenu.duplicate])
    XCTAssertEqual(system.setValues, ["Source - a1b2c3 - Before AI"])
    XCTAssertEqual(system.confirmations, [.duplicate])
  }

  func testMutationRejectsWrongActiveProjectBeforePressingMenu() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected.renamed("Different"))

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: expected, name: "Source - copy", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .identityMismatch)
    }
    XCTAssertTrue(system.menuPaths.isEmpty)
  }

  func testDuplicateRejectsAmbiguousPostcondition() {
    let source = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: source)
    system.duplicateMatchCount = 2

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: source, name: "Source - copy", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .ambiguousProject)
    }
  }

  func testExportRejectsPathOutsideSession() {
    XCTAssertThrowsError(try SessionPath(root: "/tmp/session").output("/tmp/other/source.fcpxml"))
    XCTAssertThrowsError(try SessionPath(root: "relative/session"))
  }

  func testExportRejectsSymlinkEscape() throws {
    let root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    let outside = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
    defer {
      try? FileManager.default.removeItem(at: root)
      try? FileManager.default.removeItem(at: outside)
    }
    try FileManager.default.createSymbolicLink(
      at: root.appendingPathComponent("escape"), withDestinationURL: outside
    )

    XCTAssertThrowsError(
      try SessionPath(root: root.path).output(
        root.appendingPathComponent("escape/source.fcpxml").path
      )
    )
  }

  func testExportWaitsForStableXMLWithMatchingIdentity() throws {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)
    system.exportSnapshots = [
      nil, .init(size: 120, modifiedAt: 1), .init(size: 120, modifiedAt: 1),
    ]
    system.exportedIdentity = expected

    let result = try Actions(system: system).exportXML(
      expected: expected, output: "/tmp/session/source.fcpxml", timeout: 4
    )

    XCTAssertEqual(result.kind, "fcpxml_export")
    XCTAssertEqual(result.project, expected)
    XCTAssertEqual(result.output, "/tmp/session/source.fcpxml")
    XCTAssertEqual(system.menuPaths, [FinalCutMenu.exportXML])
    XCTAssertEqual(system.setValues, ["/tmp/session/source.fcpxml"])
    XCTAssertEqual(system.confirmations, [.exportXML])
  }

  func testExportRejectsXMLForAnotherProject() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)
    system.exportSnapshots = [
      nil, .init(size: 120, modifiedAt: 1), .init(size: 120, modifiedAt: 1),
    ]
    system.exportedIdentity = expected.renamed("Wrong")

    XCTAssertThrowsError(
      try Actions(system: system).exportXML(
        expected: expected, output: "/tmp/session/source.fcpxml", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .identityMismatch)
    }
  }

  func testExportRefusesToReplaceExistingSessionFile() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)
    system.exportSnapshots = [.init(size: 120, modifiedAt: 1)]

    XCTAssertThrowsError(
      try Actions(system: system).exportXML(
        expected: expected, output: "/tmp/session/source.fcpxml", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .outputAlreadyExists)
    }
    XCTAssertTrue(system.menuPaths.isEmpty)
  }

  func testImportOpensOnlySessionCandidateAndPollsExactIdentity() throws {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: nil)
    system.importMatchCount = 1

    let result = try Actions(system: system).importXML(
      expected: expected, source: "/tmp/session/pass-01.fcpxml", timeout: 3
    )

    XCTAssertEqual(result, expected)
    XCTAssertEqual(system.openedDocuments, ["/tmp/session/pass-01.fcpxml"])
  }

  func testImportFailsClosedOnMissingMediaDialog() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: nil)
    system.dialogs = [.init(role: "AXSheet", title: "Missing Media")]

    XCTAssertThrowsError(
      try Actions(system: system).importXML(
        expected: expected, source: "/tmp/session/pass-01.fcpxml", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .blockingDialog)
    }
  }

  func testOpenSelectsExactProjectAndPollsUntilActive() throws {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected.renamed("Other"))
    system.importMatchCount = 1
    system.openedProject = expected

    let result = try Actions(system: system).openProject(expected: expected, timeout: 2)

    XCTAssertEqual(result, expected)
    XCTAssertEqual(system.selectedProjects, [expected])
  }

  func testShareWaitsForStableMovieAndBackgroundCompletion() throws {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)
    system.shareSnapshots = [
      nil,
      .init(size: 8_192, modifiedAt: 1),
      .init(size: 8_192, modifiedAt: 1),
      .init(size: 8_192, modifiedAt: 1),
      .init(size: 8_192, modifiedAt: 1),
    ]
    system.backgroundStates = [false, false, false, true]

    let result = try Actions(system: system).sharePreview(
      expected: expected, output: "/tmp/session/pass-01.mov", timeout: 30
    )

    XCTAssertEqual(result.kind, "final_cut_share")
    XCTAssertEqual(result.project, expected)
    XCTAssertEqual(result.output, "/tmp/session/pass-01.mov")
    XCTAssertEqual(system.menuPaths, [FinalCutMenu.share])
    XCTAssertEqual(system.confirmations, [.shareNext, .shareSave])
  }

  func testShareTimesOutWhenBackgroundWorkNeverCompletes() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)
    system.shareSnapshots =
      [nil]
      + Array(repeating: .init(size: 8_192, modifiedAt: 1), count: 8)
    system.backgroundStates = Array(repeating: false, count: 8)

    XCTAssertThrowsError(
      try Actions(system: system).sharePreview(
        expected: expected, output: "/tmp/session/pass-01.mov", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .timedOut)
    }
  }

  func testShareRefusesToReplaceExistingSessionMovie() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)
    system.shareSnapshots = [.init(size: 8_192, modifiedAt: 1)]

    XCTAssertThrowsError(
      try Actions(system: system).sharePreview(
        expected: expected, output: "/tmp/session/pass-01.mov", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .outputAlreadyExists)
    }
    XCTAssertTrue(system.menuPaths.isEmpty)
  }

  func testActionRejectsTimeoutThatCannotFormABoundedDeadline() {
    let expected = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: expected)

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: expected, name: "Source - copy", timeout: .greatestFiniteMagnitude
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .invalidTimeout)
    }
    XCTAssertTrue(system.menuPaths.isEmpty)
  }

  func testInspectDialogsReturnsOnlySanitizedRolesAndTitles() throws {
    let system = FakeActionSystem(active: nil)
    system.dialogs = [
      .init(role: "AXSheet\u{0000}", title: " Missing\nMedia "),
      .init(role: "AXDialog", title: "Relink Files"),
    ]

    let dialogs = try Actions(system: system).inspectDialogs()

    XCTAssertEqual(
      dialogs,
      [
        .init(role: "AXSheet", title: "Missing Media"),
        .init(role: "AXDialog", title: "Relink Files"),
      ]
    )
  }

  func testTimelineDurationRejectsOutOfRangeTimecodeComponents() {
    XCTAssertFalse(
      LiveTimelineStatus(project: "Pass 1", hours: 0, minutes: 60, seconds: 0, frames: 0)
        .matches(duration: 3_600)
    )
    XCTAssertFalse(
      LiveTimelineStatus(project: "Pass 1", hours: 0, minutes: 0, seconds: 0, frames: 60)
        .matches(duration: 1)
    )
  }
}

private final class FakeActionSystem: FinalCutActionSystem, FinalCutSystem {
  let sessionRoot = "/tmp/session"
  var active: ProjectIdentity?
  var duplicateMatchCount = 1
  var importMatchCount = 0
  var menuPaths: [[String]] = []
  var setValues: [String] = []
  var confirmations: [FinalCutConfirmation] = []
  var openedDocuments: [String] = []
  var selectedProjects: [ProjectIdentity] = []
  var openedProject: ProjectIdentity?
  var dialogs: [BlockingDialog] = []
  var exportSnapshots: [ActionFileSnapshot?] = []
  var shareSnapshots: [ActionFileSnapshot?] = []
  var backgroundStates: [Bool] = []
  var exportedIdentity: ProjectIdentity?
  var elapsed: TimeInterval = 0
  var automationAuthorized = true
  var activeProjectReads = 0
  var dialogReads = 0

  init(active: ProjectIdentity?) {
    self.active = active
  }

  func activeProject() throws -> ProjectIdentity? {
    activeProjectReads += 1
    if let openedProject {
      active = openedProject
    }
    return active
  }

  func projectMatchCount(_ identity: ProjectIdentity) throws -> Int {
    if identity.project.contains("copy") || identity.project.contains("Before AI") {
      if duplicateMatchCount == 1 {
        active = identity
      }
      return duplicateMatchCount
    }
    if importMatchCount == 1 {
      active = identity
    }
    return importMatchCount
  }

  func pressMenu(path: [String]) throws {
    menuPaths.append(path)
  }

  func setExpectedSheetValue(_ value: String) throws {
    setValues.append(value)
  }

  func confirmExpectedSheet(_ confirmation: FinalCutConfirmation) throws {
    confirmations.append(confirmation)
  }

  func openDocument(_ path: String) throws {
    openedDocuments.append(path)
  }

  func selectProject(_ identity: ProjectIdentity) throws {
    selectedProjects.append(identity)
  }

  func fileSnapshot(_ path: String) throws -> ActionFileSnapshot? {
    if path.hasSuffix(".fcpxml") {
      return exportSnapshots.isEmpty ? nil : exportSnapshots.removeFirst()
    }
    return shareSnapshots.isEmpty ? nil : shareSnapshots.removeFirst()
  }

  func identityOfExport(at path: String, expected: ProjectIdentity) throws -> ProjectIdentity? {
    exportedIdentity
  }

  func backgroundTasksComplete() throws -> Bool {
    backgroundStates.isEmpty ? false : backgroundStates.removeFirst()
  }

  func blockingDialogs() throws -> [BlockingDialog] {
    dialogReads += 1
    return dialogs
  }

  func runningApplications(bundleIdentifier: String) -> [FinalCutApplication] {
    [
      .init(
        bundleIdentifier: "com.apple.FinalCutApp", version: "12.3", processIdentifier: 100
      )
    ]
  }

  func isAccessibilityTrusted() -> Bool {
    true
  }

  func readLibraryNames(processIdentifier: pid_t) throws -> [String] {
    guard automationAuthorized else {
      throw FinalCutAutomationError.notAuthorized
    }
    return ["Canary"]
  }

  func monotonicTime() -> TimeInterval {
    elapsed
  }

  func waitForPoll() {
    elapsed += 1
  }
}

extension ProjectIdentity {
  fileprivate static let canaryCandidate = ProjectIdentity(
    library: "Canary", event: "Event", project: "Pass 1", duration: 8
  )

  fileprivate func renamed(_ name: String) -> ProjectIdentity {
    ProjectIdentity(library: library, event: event, project: name, duration: duration)
  }
}

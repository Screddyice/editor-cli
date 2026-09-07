import ApplicationServices
import Foundation
import XCTest

@testable import FinalCutBridge

final class ActionTests: XCTestCase {
  func testExplicitProjectInspectionPayloadDecodesWithoutExpectedIdentity() throws {
    let request = try StrictProtocol.decodeRequest(Data(
      #"{"protocolVersion":1,"action":"inspect_active_project","sessionRoot":"/tmp/session","payload":{"timeout":2}}"#.utf8
    ))
    XCTAssertNoThrow(try ActionPayload.decode(request))
  }

  func testReadyProbeNeverReadsOrRevealsTheActiveProject() throws {
    let system = FakeActionSystem(active: .canaryCandidate)
    let result = try FinalCutProbe(system: system).run()
    XCTAssertTrue(result.ready)
    XCTAssertNil(result.activeProject)
    XCTAssertEqual(system.activeProjectReads, 0)
    XCTAssertTrue(system.menuPaths.isEmpty)
  }
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
    system.duplicateMatchCounts = [0, 2]

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: source, name: "Source - copy", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .ambiguousProject)
    }
  }

  func testDuplicateRejectsSourceNameBeforeMutation() {
    let source = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: source)

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: source, name: source.project, timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .invalidName)
    }
    XCTAssertTrue(system.menuPaths.isEmpty)
  }

  func testDuplicateRequiresAbsentTargetBeforeMutation() {
    let source = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: source)
    system.duplicateMatchCounts = [1]

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: source, name: "Pass 1 copy", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .ambiguousProject)
    }
    XCTAssertTrue(system.menuPaths.isEmpty)
  }

  func testDuplicateDeadlineIncludesMutationOperations() {
    let source = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: source)
    system.duplicateMatchCounts = [0, 1]
    system.menuTimeCost = 3

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: source, name: "Pass 1 copy", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .timedOut)
    }
    XCTAssertTrue(system.setValues.isEmpty)
    XCTAssertTrue(system.confirmations.isEmpty)
  }

  func testDuplicatePollRejectsSuccessReachedAfterDeadline() {
    let source = ProjectIdentity.canaryCandidate
    let system = FakeActionSystem(active: source)
    system.duplicateMatchCounts = [0, 1]
    system.activeReadTimeCosts = [0, 3]

    XCTAssertThrowsError(
      try Actions(system: system).duplicateProject(
        expected: source, name: "Pass 1 copy", timeout: 2
      )
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .timedOut)
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

  func testTimelineDurationUsesReportedProjectTimebase() throws {
    let timebase = try XCTUnwrap(
      FinalCutTimebase(formatDescription: "1920 x 1080 | 23.98p")
    )
    let status = LiveTimelineStatus(
      project: "Pass 1", hours: 0, minutes: 0, seconds: 0, frames: 12,
      timebase: timebase
    )

    XCTAssertTrue(status.matches(duration: 12 * 1_001 / 24_000))
    XCTAssertFalse(status.matches(duration: 0.5))
  }

  func testFractionalProjectTimebaseUsesCanonicalFrameCount() throws {
    let timebase = try XCTUnwrap(
      FinalCutTimebase(formatDescription: "1920 x 1080 | 23.98p")
    )
    let status = LiveTimelineStatus(
      project: "Pass 1", hours: 0, minutes: 1, seconds: 0, frames: 0,
      timebase: timebase
    )

    XCTAssertTrue(status.matches(duration: 1_440 * 1_001 / 24_000))
    XCTAssertFalse(status.matches(duration: 60))
  }

  func testTimelineDurationRejectsOutOfRangeTimecodeComponents() throws {
    let timebase = try XCTUnwrap(FinalCutTimebase(formatDescription: "1920 x 1080 | 25p"))
    XCTAssertFalse(
      LiveTimelineStatus(
        project: "Pass 1", hours: 0, minutes: 60, seconds: 0, frames: 0,
        timebase: timebase
      )
      .matches(duration: 3_600)
    )
    XCTAssertFalse(
      LiveTimelineStatus(
        project: "Pass 1", hours: 0, minutes: 0, seconds: 0, frames: 25,
        timebase: timebase
      )
      .matches(duration: 1)
    )
  }

  func testActiveTimelineStatusUsesKnownProjectFormatControlInFocusedWindow() throws {
    let projectTitle = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "Pass 1",
      identifier: "timelineProjectTitle"
    )
    let projectDuration = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "00:00:00:05",
      identifier: "timelineDuration"
    )
    let projectFormat = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "1920 x 1080 | 23.98p",
      identifier: FinalCutAXIdentifier.activeProjectFormat
    )
    let clipFormatDecoy = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "3840 x 2160 | 24p",
      identifier: "clipInspectorFormat"
    )
    let otherWindowFormatDecoy = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "3840 x 2160 | 29.97p",
      identifier: FinalCutAXIdentifier.activeProjectFormat
    )
    let projectContent = FakeFinalCutAXElement.group(
      children: [projectTitle, projectDuration, projectFormat]
    )
    let root = FakeFinalCutAXElement.application(children: [
      .window(
        title: "Final Cut Pro",
        children: [projectContent, clipFormatDecoy]
      ),
      .window(title: "Other", children: [otherWindowFormatDecoy]),
    ])

    let status = try XCTUnwrap(
      LiveFinalCutAX(root: root).activeTimelineStatus(timeout: 2)
    )

    XCTAssertEqual(status.timebase, FinalCutTimebase(formatDescription: "23.98p"))
    XCTAssertTrue(status.matches(duration: 5 * 1_001 / 24_000))
  }

  func testCreatorStudioTimelineUsesProjectToolbarAndMatchingViewer() throws {
    let root = creatorStudioTimeline(duration: "08:00")
    let status = try XCTUnwrap(LiveFinalCutAX(root: root).activeTimelineStatus(timeout: 2))
    XCTAssertEqual(status.project, "Canary Unique")
    XCTAssertEqual(status.duration, 8)
  }

  func testCreatorStudioTimelineRejectsViewerShowingAnotherClip() throws {
    let root = creatorStudioTimeline(duration: "08:00", viewerTitle: "Source Clip")
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).activeTimelineStatus(timeout: 2))
  }

  func testCreatorStudioTimelineRejectsSelectionSummaryAsDuration() throws {
    let root = creatorStudioTimeline(duration: "02:00 / 08:00")
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).activeTimelineStatus(timeout: 2))
  }

  func testCreatorStudioTimelineParsesTrimmedMinuteTimecode() throws {
    let root = creatorStudioTimeline(duration: "01:02:15")
    let status = try XCTUnwrap(LiveFinalCutAX(root: root).activeTimelineStatus(timeout: 2))
    XCTAssertEqual(status.duration, 62.5)
  }

  private func creatorStudioTimeline(
    duration: String, viewerTitle: String = "Canary Unique"
  ) -> FakeFinalCutAXElement {
    let title = FakeFinalCutAXElement(
      role: "AXMenuButton", title: "Canary Unique",
      identifier: "editor/timelineContainer/toolbar/projectNamePopUpButton"
    )
    let info = FakeFinalCutAXElement(
      role: "AXStaticText", identifier: "editor/timelineContainer/toolbar/projectInfo",
      description: "ProjectInfo"
    )
    _ = info.accessibilitySetString(duration, for: "AXValue")
    let timeline = FakeFinalCutAXElement.group(children: [
      .init(role: "AXLayoutArea", description: "Project Timeline"), title, info,
    ])
    let viewer = FakeFinalCutAXElement.group(children: [
      .init(role: "AXStaticText", title: "1080p HD 30p, Stereo"),
      .init(role: "AXImage", description: "F Viewer PlayerLabels Projects"),
      .init(role: "AXStaticText", title: viewerTitle),
    ])
    let inspectorDecoy = FakeFinalCutAXElement.group(children: [
      .init(role: "AXStaticText", title: "4K 60p, Stereo")
    ])
    return .application(children: [
      .window(title: "Final Cut Pro", children: [timeline, viewer, inspectorDecoy])
    ])
  }

  func testActiveTimelineStatusRejectsProjectFormatDecoyOutsideProjectAncestry() throws {
    let projectTitle = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "Pass 1",
      identifier: "timelineProjectTitle"
    )
    let projectDuration = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "00:00:00:05",
      identifier: "timelineDuration"
    )
    let decoyFormat = FakeFinalCutAXElement(
      role: kAXStaticTextRole as String,
      title: "1920 x 1080 | 23.98p",
      identifier: FinalCutAXIdentifier.activeProjectFormat
    )
    let projectContent = FakeFinalCutAXElement.group(
      children: [projectTitle, projectDuration]
    )
    let inspector = FakeFinalCutAXElement.group(children: [decoyFormat])
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: [projectContent, inspector])
    ])

    XCTAssertThrowsError(
      try LiveFinalCutAX(root: root).activeTimelineStatus(timeout: 2)
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .ambiguousMatch)
    }
  }

  func testActiveProjectMatchAcceptsEquivalentFCPXMLFractionalDuration() throws {
    let directory = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    let source = directory.appendingPathComponent("fractional.fcpxml")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let xml =
      #"<fcpxml version="1.13"><library><event name="Event"><project name="Pass 1"><sequence duration="5005/24000s"/></project></event></library></fcpxml>"#
    try Data(xml.utf8).write(to: source)
    let exported = try XCTUnwrap(FCPXMLProjectReader().read(path: source.path))
    let timebase = try XCTUnwrap(FinalCutTimebase(formatDescription: "1920 x 1080 | 23.98p"))
    let status = LiveTimelineStatus(
      project: "Pass 1", hours: 0, minutes: 0, seconds: 0, frames: 5,
      timebase: timebase
    )
    let location = FinalCutProjectLocation(
      library: "Canary", event: "Event", project: "Pass 1"
    )
    let expected = ProjectIdentity(
      library: location.library,
      event: location.event,
      project: exported.project,
      duration: exported.duration
    )

    XCTAssertNotEqual(status.duration, exported.duration)
    XCTAssertTrue(
      ActiveProjectResolver.matches(status: status, location: location, expected: expected)
    )
    XCTAssertFalse(
      ActiveProjectResolver.matches(
        status: status,
        location: location,
        expected: ProjectIdentity(
          library: expected.library,
          event: expected.event,
          project: expected.project,
          duration: expected.duration + timebase.frameDuration
        )
      )
    )
  }

  func testOpenProjectAcceptsEquivalentFractionalFCPXMLDuration() throws {
    let expectedDuration = 5_005.0 / 24_000.0
    let expected = ProjectIdentity(
      library: "Canary", event: "Event", project: "Pass 1", duration: expectedDuration
    )
    let system = FakeActionSystem(
      active: ProjectIdentity(
        library: expected.library,
        event: expected.event,
        project: expected.project,
        duration: expected.duration.nextUp
      )
    )
    system.importMatchCount = 1

    let result = try Actions(system: system).openProject(expected: expected, timeout: 2)

    XCTAssertEqual(result, expected)
  }

  func testActionSystemMatchUsesFractionalDurationToleranceThroughExistential() throws {
    let expected = ProjectIdentity(
      library: "Canary", event: "Event", project: "Pass 1", duration: 5_005.0 / 24_000.0
    )
    let system: any FinalCutActionSystem = FakeActionSystem(
      active: ProjectIdentity(
        library: expected.library,
        event: expected.event,
        project: expected.project,
        duration: expected.duration.nextUp
      )
    )

    XCTAssertTrue(try system.activeProjectMatches(expected, timeout: 2))

    let materiallyDifferent: any FinalCutActionSystem = FakeActionSystem(
      active: ProjectIdentity(
        library: expected.library,
        event: expected.event,
        project: expected.project,
        duration: expected.duration + (1.0 / 24.0)
      )
    )
    XCTAssertFalse(try materiallyDifferent.activeProjectMatches(expected, timeout: 2))
  }

  func testActiveProjectResolutionRejectsSameNameAcrossLocations() throws {
    let timebase = try XCTUnwrap(FinalCutTimebase(formatDescription: "1920 x 1080 | 25p"))
    let status = LiveTimelineStatus(
      project: "Pass 1", hours: 0, minutes: 0, seconds: 8, frames: 0,
      timebase: timebase
    )
    let locations = [
      FinalCutProjectLocation(library: "Canary", event: "Event", project: "Pass 1"),
      FinalCutProjectLocation(library: "Other", event: "Event", project: "Pass 1"),
    ]

    XCTAssertThrowsError(
      try ActiveProjectResolver.resolve(status: status, locations: locations)
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .ambiguousProject)
    }
  }

  func testActiveProjectResolutionRejectsSameNameAcrossEvents() throws {
    let timebase = try XCTUnwrap(FinalCutTimebase(formatDescription: "1920 x 1080 | 25p"))
    let status = LiveTimelineStatus(
      project: "Pass 1", hours: 0, minutes: 0, seconds: 8, frames: 0,
      timebase: timebase
    )
    let locations = [
      FinalCutProjectLocation(library: "Canary", event: "Event", project: "Pass 1"),
      FinalCutProjectLocation(library: "Canary", event: "Other", project: "Pass 1"),
    ]

    XCTAssertThrowsError(
      try ActiveProjectResolver.resolve(status: status, locations: locations)
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .ambiguousProject)
    }
  }

  func testProjectSelectionScopesFlatEventRowsToTheirLibrary() throws {
    let (root, target, decoy, tile, open) = flatBrowser()
    try LiveFinalCutAX(root: root).pressProjectRow(.canaryCandidate, timeout: 0.1)
    XCTAssertEqual(target.accessibilityValue(for: "AXSelected") as? Bool, true)
    XCTAssertNil(decoy.accessibilityValue(for: "AXSelected"))
    XCTAssertTrue(tile.pressed)
    XCTAssertFalse(open.pressed)
  }

  func testProjectSelectionRejectsMissingLibraryWithoutSelectingAnotherEvent() throws {
    let (root, target, decoy, tile, open) = flatBrowser()
    let missing = ProjectIdentity(library: "Missing", event: "Event", project: "Pass 1", duration: 8)
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).pressProjectRow(missing, timeout: 0.1))
    XCTAssertNil(target.accessibilityValue(for: "AXSelected"))
    XCTAssertNil(decoy.accessibilityValue(for: "AXSelected"))
    XCTAssertFalse(tile.pressed)
    XCTAssertFalse(open.pressed)
  }

  func testProjectSelectionDoesNotPressStaleSameNameTileAfterEventChange() throws {
    let (root, _, _, tile, _) = flatBrowser(staleNavigation: true)
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).pressProjectRow(.canaryCandidate, timeout: 0.1))
    XCTAssertFalse(tile.pressed)
  }

  func testActiveProjectRevealBindsTimelineToSelectedBrowserScope() throws {
    let (root, _, decoy, _, _) = flatBrowser()
    decoy.setTestAttribute("AXSelected", true)
    let location = try XCTUnwrap(LiveFinalCutAX(root: root).revealActiveProjectLocation(timeout: 1))
    XCTAssertEqual(location, FinalCutProjectLocation(library: "Canary", event: "Event", project: "Pass 1"))
  }

  func testActiveProjectRevealRejectsSelectedTileWithDifferentName() throws {
    let (root, _, _, tile, _) = flatBrowser()
    tile.setTestAttribute("AXDescription", "Other Project")
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).revealActiveProjectLocation(timeout: 0.1))
  }

  func testActiveProjectRevealRejectsTimelineSwitchDuringReveal() throws {
    let (root, _, _, _, _) = flatBrowser(changeTimeline: true)
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).revealActiveProjectLocation(timeout: 0.1))
  }

  func testActiveProjectRevealRejectsPreselectedSameNameWhenRevealDoesNothing() throws {
    let (root, _, _, _, _) = flatBrowser(noOpReveal: true)
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).revealActiveProjectLocation(timeout: 0.1))
  }

  func testMenuTraversalUsesDirectMenuInsteadOfNestedSubmenus() throws {
    let reveal = FakeFinalCutAXElement.menuItem("Reveal Project in Browser")
    let unrelated = FakeFinalCutAXElement.menuItem("New", children: [.menu(children: [.menuItem("Project…")])])
    let root = FakeFinalCutAXElement.application(children: [.menuBar(children: [
      .menuBarItem("File", children: [.menu(children: [unrelated, reveal])])
    ])])
    try LiveFinalCutAX(root: root).pressMenu(path: ["File", "Reveal Project in Browser"], timeout: 0.1)
    XCTAssertTrue(reveal.pressed)
    XCTAssertFalse(unrelated.pressed)
  }

  func testMutationMenusReachCreatorStudioCommands() throws {
    let duplicate = FakeFinalCutAXElement.menuItem("Duplicate Project As…")
    let export = FakeFinalCutAXElement.menuItem("Export XML…")
    let share = FakeFinalCutAXElement.menuItem("Export File (default)…")
    let root = FakeFinalCutAXElement.application(children: [.menuBar(children: [
      .menuBarItem("Edit", children: [.menu(children: [duplicate])]),
      .menuBarItem("File", children: [.menu(children: [export,
        .menuItem("Share", children: [.menu(children: [share])])])])
    ])])
    let ax = LiveFinalCutAX(root: root)
    try ax.pressMenu(path: FinalCutMenu.duplicate, timeout: 0.1)
    try ax.pressMenu(path: FinalCutMenu.exportXML, timeout: 0.1)
    try ax.pressMenu(path: FinalCutMenu.share, timeout: 0.1)
    XCTAssertTrue(duplicate.pressed)
    XCTAssertTrue(export.pressed)
    XCTAssertTrue(share.pressed)
  }

  func testDisabledMenuCommandReportsDisabledRatherThanMissing() {
    let duplicate = FakeFinalCutAXElement.menuItem("Duplicate Project As\u{2026}")
    duplicate.setTestAttribute(kAXEnabledAttribute as String, false)
    let edit = FakeFinalCutAXElement.menuBarItem("Edit", children: [.menu(children: [duplicate])])
    let root = FakeFinalCutAXElement.application(children: [.menuBar(children: [edit])])

    XCTAssertThrowsError(
      try LiveFinalCutAX(root: root).pressMenu(path: FinalCutMenu.duplicate, timeout: 0.2)
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .disabledControl)
    }
    XCTAssertFalse(duplicate.pressed)
  }

  func testFailedMenuTraversalClosesTheMenuItLeftOpen() {
    let export = FakeFinalCutAXElement.menuItem("Export File (default)\u{2026}")
    let wrongShare = FakeFinalCutAXElement(
      role: kAXButtonRole as String, title: "Share", children: [export]
    )
    let file = FakeFinalCutAXElement.menuBarItem(
      "File", children: [.menu(children: [wrongShare])]
    )
    let root = FakeFinalCutAXElement.application(children: [.menuBar(children: [file])])

    XCTAssertThrowsError(
      try LiveFinalCutAX(root: root).pressMenu(path: FinalCutMenu.share, timeout: 0.2)
    )
    XCTAssertTrue(file.cancelled)
  }

  func testActiveProjectRevealRejectsHiddenSelectedEvent() throws {
    let (root, target, _, _, _) = flatBrowser()
    target.setTestAttribute("AXHidden", true)
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).revealActiveProjectLocation(timeout: 0.1))
  }

  func testRevealRequiresVerifiedDeselectBeforeRevealing() throws {
    let (root, _, _, _, _) = flatBrowser(noOpDeselect: true)
    XCTAssertThrowsError(try LiveFinalCutAX(root: root).revealActiveProjectLocation(timeout: 0.1))
  }

  private func flatBrowser(changeTimeline: Bool = false, noOpReveal: Bool = false,
    staleNavigation: Bool = false, noOpDeselect: Bool = false) -> (
    FakeFinalCutAXElement, FakeFinalCutAXElement, FakeFinalCutAXElement,
    FakeFinalCutAXElement, FakeFinalCutAXElement
  ) {
    func row(_ name: String, level: Double) -> FakeFinalCutAXElement {
      let field = FakeFinalCutAXElement(role: "AXTextField", title: name)
      let row = FakeFinalCutAXElement(
        role: "AXRow", children: [.init(role: "AXCell", children: [.init(role: "AXCell", children: [field])])]
      )
      row.setTestAttribute("AXDisclosureLevel", level)
      row.setTestAttribute("AXDisclosing", true)
      return row
    }
    let target = row("Event", level: 1)
    let decoy = row("Event", level: 1)
    let outline = FakeFinalCutAXElement(role: "AXOutline", description: "Event media sidebar", children: [
      row("Other", level: 0), decoy, row("Canary", level: 0), target,
    ])
    let tile = FakeFinalCutAXElement(role: "AXGroup", description: "Pass 1", children: [
      .init(role: "AXTextField", title: "Pass 1", description: "display name")
    ])
    let oldTile = staleNavigation ? tile : FakeFinalCutAXElement(role: "AXGroup", description: "Pass 1",
      children: [.init(role: "AXTextField", title: "Pass 1", description: "display name")])
    let events = FakeFinalCutAXElement(role: "AXGroup", title: "events", children: [oldTile])
    target.onSetBool = { value, attribute in
      if value && attribute == "AXSelected" && !staleNavigation { events.children = [tile] }
    }
    let browser = FakeFinalCutAXElement(role: "AXScrollArea", description: "organizer", children: [
      events
    ])
    browser.setTestAttribute("AXEnabled", NSNull())
    let open = FakeFinalCutAXElement.menuItem("Open Clip")
    let reveal = FakeFinalCutAXElement.menuItem("Reveal Project in Browser")
    let deselect = FakeFinalCutAXElement.menuItem("Deselect All")
    deselect.onPress = {
      if !noOpDeselect { events.setTestElements("AXSelectedChildren", []) }
    }
    let projectTitle = FakeFinalCutAXElement(role: "AXMenuButton", title: "Pass 1",
      identifier: "editor/timelineContainer/toolbar/projectNamePopUpButton")
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: [outline, browser, projectTitle]),
      .menuBar(children: [
        .menuBarItem("Clip", children: [.menu(children: [open])]),
        .menuBarItem("File", children: [.menu(children: [reveal])]),
        .menuBarItem("Edit", children: [.menu(children: [deselect])]),
      ]),
    ])
    events.setTestElements("AXSelectedChildren", [oldTile])
    root.setTestElement("AXFocusedUIElement", events)
    if noOpReveal { decoy.setTestAttribute("AXSelected", true) }
    reveal.onPress = {
      if noOpReveal { return }
      events.children = [tile]
      target.setTestAttribute("AXSelected", true)
      decoy.setTestAttribute("AXSelected", false)
      events.setTestElements("AXSelectedChildren", [tile])
      root.setTestElement("AXFocusedUIElement", events)
      if changeTimeline { projectTitle.setTestAttribute("AXTitle", "Different Timeline") }
    }
    return (root, target, decoy, tile, open)
  }

  func testExportPanelIsFoundWhenCreatorStudioFloatsItAsItsOwnDialog() throws {
    let field = FakeFinalCutAXElement.textField()
    let panel = FakeFinalCutAXElement(
      role: kAXWindowRole as String, title: "Export XML", subrole: "AXDialog",
      children: [field, .button("Save")]
    )
    // The panel takes focus, so the main window is no longer the focused one.
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: []), panel,
    ])

    try LiveFinalCutAX(root: root).setUniqueVisibleTextField(
      "/tmp/session/source.fcpxml", stage: .exportXML, timeout: 2
    )

    XCTAssertEqual(field.writtenValue, "/tmp/session/source.fcpxml")
  }

  func testExportSheetRequiresExactSheetUnderMainWindow() throws {
    let field = FakeFinalCutAXElement.textField()
    let exportSheet = FakeFinalCutAXElement.sheet(
      title: "Export XML", children: [field, .button("Save")]
    )
    let unrelatedField = FakeFinalCutAXElement.textField()
    let unrelatedWindow = FakeFinalCutAXElement.window(
      title: "Other", children: [unrelatedField, .button("Save")]
    )
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: [exportSheet]), unrelatedWindow,
    ])
    let controller = LiveFinalCutAX(root: root)

    try controller.setUniqueVisibleTextField(
      "/tmp/session/source.fcpxml", stage: .exportXML, timeout: 2
    )

    XCTAssertEqual(field.writtenValue, "/tmp/session/source.fcpxml")
    XCTAssertNil(unrelatedField.writtenValue)
  }

  func testShareSettingsRequiresKnownModalMarker() throws {
    let next = FakeFinalCutAXElement.button("Next...")
    let shareWindow = FakeFinalCutAXElement.dialog(children: [
      .staticText(description: FinalCutAXIdentifier.shareWindowBackground), next,
    ])
    let decoy = FakeFinalCutAXElement.dialog(children: [.button("Next...")])
    let root = FakeFinalCutAXElement.application(children: [shareWindow, decoy])

    try LiveFinalCutAX(root: root).pressUniqueEnabledButton(stage: .shareSettings, timeout: 2)

    XCTAssertTrue(next.pressed)
    XCTAssertFalse(decoy.children[0].pressed)
  }

  func testShareSaveSheetStaysUnderKnownShareWindow() throws {
    let field = FakeFinalCutAXElement.textField()
    let shareWindow = FakeFinalCutAXElement.dialog(children: [
      .staticText(description: FinalCutAXIdentifier.shareWindowBackground),
      .sheet(title: "Save", children: [field, .button("Save")]),
    ])
    let decoyField = FakeFinalCutAXElement.textField()
    let decoyWindow = FakeFinalCutAXElement.window(
      title: "Other",
      children: [.sheet(title: "Save", children: [decoyField, .button("Save")])]
    )
    let root = FakeFinalCutAXElement.application(children: [shareWindow, decoyWindow])

    try LiveFinalCutAX(root: root).setUniqueVisibleTextField(
      "/tmp/session/pass-01.mov", stage: .shareSave, timeout: 2
    )

    XCTAssertEqual(field.writtenValue, "/tmp/session/pass-01.mov")
    XCTAssertNil(decoyField.writtenValue)
  }

  func testMissingKnownBackgroundTaskIndicatorFailsClosed() throws {
    let root = FakeFinalCutAXElement.application(children: [])

    XCTAssertFalse(try LiveFinalCutAX(root: root).backgroundTasksComplete(timeout: 2))
  }

  func testKnownCompletedBackgroundTaskIndicatorIsAffirmative() throws {
    let indicator = FakeFinalCutAXElement.progress(
      identifier: FinalCutAXIdentifier.backgroundTaskProgress, value: 1, maximum: 1
    )
    let root = FakeFinalCutAXElement.application(children: [indicator])

    XCTAssertTrue(try LiveFinalCutAX(root: root).backgroundTasksComplete(timeout: 2))
  }

  func testBackgroundTaskIndicatorRejectsValuePastMaximum() throws {
    let indicator = FakeFinalCutAXElement.progress(
      identifier: FinalCutAXIdentifier.backgroundTaskProgress, value: 2, maximum: 1
    )
    let root = FakeFinalCutAXElement.application(children: [indicator])

    XCTAssertFalse(try LiveFinalCutAX(root: root).backgroundTasksComplete(timeout: 2))
  }

  func testMenuTraversalRejectsWrongRoleAtIntermediateHop() {
    let export = FakeFinalCutAXElement.menuItem("Export File (default)…")
    let wrongShare = FakeFinalCutAXElement(
      role: kAXButtonRole as String, title: "Share", children: [export]
    )
    let file = FakeFinalCutAXElement.menuBarItem(
      "File", children: [.menu(children: [wrongShare])]
    )
    let root = FakeFinalCutAXElement.application(children: [.menuBar(children: [file])])

    XCTAssertThrowsError(
      try LiveFinalCutAX(root: root).pressMenu(path: FinalCutMenu.share, timeout: 2)
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .unexpectedFinalRole)
    }
    XCTAssertFalse(export.pressed)
  }
}

private final class FakeActionSystem: FinalCutActionSystem, FinalCutSystem {
  let sessionRoot = "/tmp/session"
  var active: ProjectIdentity?
  var duplicateMatchCounts = [0, 1]
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
  var menuTimeCost: TimeInterval = 0
  var activeReadTimeCosts: [TimeInterval] = []

  init(active: ProjectIdentity?) {
    self.active = active
  }

  func activeProject(timeout: TimeInterval) throws -> ProjectIdentity? {
    activeProjectReads += 1
    if !activeReadTimeCosts.isEmpty {
      elapsed += activeReadTimeCosts.removeFirst()
    }
    if let openedProject {
      active = openedProject
    }
    return active
  }

  func activeProjectMatches(
    _ expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> Bool {
    guard let active = try activeProject(timeout: timeout) else { return false }
    return active.library == expected.library
      && active.event == expected.event
      && active.project == expected.project
      && abs(active.duration - expected.duration) < 0.000_001
  }

  func projectMatchCount(_ identity: ProjectIdentity, timeout: TimeInterval) throws -> Int {
    if identity.project.contains("copy") || identity.project.contains("Before AI") {
      let count = duplicateMatchCounts.isEmpty ? 0 : duplicateMatchCounts.removeFirst()
      if count == 1 {
        active = identity
      }
      return count
    }
    if importMatchCount == 1 {
      active = identity
    }
    return importMatchCount
  }

  func pressMenu(path: [String], timeout: TimeInterval) throws {
    menuPaths.append(path)
    elapsed += menuTimeCost
  }

  func setExpectedSheetValue(_ value: String, timeout: TimeInterval) throws {
    setValues.append(value)
  }

  func confirmExpectedSheet(
    _ confirmation: FinalCutConfirmation, timeout: TimeInterval
  ) throws {
    confirmations.append(confirmation)
  }

  func openDocument(_ path: String, timeout: TimeInterval) throws {
    openedDocuments.append(path)
  }

  func selectProject(_ identity: ProjectIdentity, timeout: TimeInterval) throws {
    selectedProjects.append(identity)
  }

  func fileSnapshot(_ path: String, timeout: TimeInterval) throws -> ActionFileSnapshot? {
    if path.hasSuffix(".fcpxml") {
      return exportSnapshots.isEmpty ? nil : exportSnapshots.removeFirst()
    }
    return shareSnapshots.isEmpty ? nil : shareSnapshots.removeFirst()
  }

  func identityOfExport(
    at path: String, expected: ProjectIdentity, timeout: TimeInterval
  ) throws -> ProjectIdentity? {
    exportedIdentity
  }

  func backgroundTasksComplete(timeout: TimeInterval) throws -> Bool {
    backgroundStates.isEmpty ? false : backgroundStates.removeFirst()
  }

  func blockingDialogs(timeout: TimeInterval) throws -> [BlockingDialog] {
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

  func waitForPoll(maximum: TimeInterval) {
    elapsed += min(1, maximum)
  }
}

private final class FakeFinalCutAXElement: FinalCutAXElement {
  private var attributes: [String: Any]
  private var elementAttributes: [String: FakeFinalCutAXElement] = [:]
  private var arrayAttributes: [String: [FakeFinalCutAXElement]] = [:]
  var children: [FakeFinalCutAXElement]
  var pressed = false
  var writtenValue: String?
  var onPress: (() -> Void)?
  var onSetBool: ((Bool, String) -> Void)?
  var cancelled = false

  func setTestAttribute(_ name: String, _ value: Any) { attributes[name] = value }
  func setTestElement(_ name: String, _ value: FakeFinalCutAXElement) { elementAttributes[name] = value }
  func setTestElements(_ name: String, _ value: [FakeFinalCutAXElement]) { arrayAttributes[name] = value }
  func accessibilityElements(for attribute: String) -> [any FinalCutAXElement] { arrayAttributes[attribute] ?? [] }

  func accessibilitySetBool(_ value: Bool, for attribute: String) -> Bool {
    attributes[attribute] = value
    onSetBool?(value, attribute)
    return true
  }


  init(
    role: String,
    title: String? = nil,
    identifier: String? = nil,
    subrole: String? = nil,
    description: String? = nil,
    modal: Bool? = nil,
    enabled: Bool = true,
    hidden: Bool = false,
    value: Double? = nil,
    maximum: Double? = nil,
    children: [FakeFinalCutAXElement] = []
  ) {
    var attributes: [String: Any] = [
      kAXRoleAttribute as String: role,
      kAXEnabledAttribute as String: enabled,
      kAXHiddenAttribute as String: hidden,
    ]
    attributes[kAXTitleAttribute as String] = title
    attributes[kAXIdentifierAttribute as String] = identifier
    attributes[kAXSubroleAttribute as String] = subrole
    attributes[kAXDescriptionAttribute as String] = description
    attributes[kAXModalAttribute as String] = modal
    attributes[kAXValueAttribute as String] = value
    attributes[kAXMaxValueAttribute as String] = maximum
    self.attributes = attributes
    self.children = children
  }

  static func application(children: [FakeFinalCutAXElement]) -> FakeFinalCutAXElement {
    let application = FakeFinalCutAXElement(
      role: kAXApplicationRole as String, children: children
    )
    if let focused = children.first(where: {
      $0.accessibilityValue(for: kAXSubroleAttribute as String) as? String
        == kAXStandardWindowSubrole as String
    }) {
      application.elementAttributes[kAXFocusedWindowAttribute as String] = focused
    }
    return application
  }

  static func window(
    title: String, children: [FakeFinalCutAXElement]
  ) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(
      role: kAXWindowRole as String,
      title: title,
      subrole: kAXStandardWindowSubrole as String,
      modal: false,
      children: children
    )
  }

  static func sheet(
    title: String, children: [FakeFinalCutAXElement]
  ) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXSheetRole as String, title: title, children: children)
  }

  static func dialog(children: [FakeFinalCutAXElement]) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(
      role: kAXWindowRole as String,
      subrole: "AXDialog",
      modal: true,
      children: children
    )
  }

  static func staticText(description: String) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(
      role: kAXStaticTextRole as String, description: description
    )
  }

  static func row(
    _ title: String, children: [FakeFinalCutAXElement] = []
  ) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXRowRole as String, title: title, children: children)
  }

  static func group(children: [FakeFinalCutAXElement]) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXGroupRole as String, children: children)
  }

  static func textField() -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXTextFieldRole as String)
  }

  static func button(_ title: String) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXButtonRole as String, title: title)
  }

  static func progress(
    identifier: String, value: Double, maximum: Double
  ) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(
      role: kAXProgressIndicatorRole as String,
      identifier: identifier,
      value: value,
      maximum: maximum
    )
  }

  static func menuBar(children: [FakeFinalCutAXElement]) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXMenuBarRole as String, children: children)
  }

  static func menuBarItem(
    _ title: String, children: [FakeFinalCutAXElement]
  ) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXMenuBarItemRole as String, title: title, children: children)
  }

  static func menu(children: [FakeFinalCutAXElement]) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXMenuRole as String, children: children)
  }

  static func menuItem(
    _ title: String, children: [FakeFinalCutAXElement] = []
  ) -> FakeFinalCutAXElement {
    FakeFinalCutAXElement(role: kAXMenuItemRole as String, title: title, children: children)
  }

  func accessibilityValue(for attribute: String) -> Any? {
    attributes[attribute]
  }

  func accessibilityElement(for attribute: String) -> (any FinalCutAXElement)? {
    elementAttributes[attribute]
  }

  func accessibilityChildren() throws -> [any FinalCutAXElement] {
    children
  }

  func accessibilityPress() -> Bool {
    pressed = true
    onPress?()
    return true
  }

  func accessibilityCancel() -> Bool {
    cancelled = true
    return true
  }

  func accessibilitySetString(_ value: String, for attribute: String) -> Bool {
    writtenValue = value
    attributes[attribute] = value
    return true
  }

  func accessibilityAttributeIsSettable(_ attribute: String) -> Bool {
    ["AXValue", "AXSelected", "AXDisclosing", "AXSelectedChildren"].contains(attribute)
  }

  func isSameElement(as other: any FinalCutAXElement) -> Bool {
    guard let other = other as? FakeFinalCutAXElement else { return false }
    return self === other
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

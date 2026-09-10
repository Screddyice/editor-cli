import Foundation
import XCTest

@testable import FinalCutBridge

final class InspectionCoordinatorTests: XCTestCase {
  func testRevealsAgainAfterMetadataBeforeReturningIdentity() throws {
    let fixture = try InspectionFixture()
    let result = try fixture.coordinator().inspect(timeout: 2)
    XCTAssertEqual(
      result,
      ProjectIdentity(library: "Library A", event: "Event", project: "Timeline", duration: 8))
    XCTAssertEqual(fixture.calls, ["verify", "reveal", "metadata", "reveal", "status"])
    XCTAssertEqual(fixture.scopesQueried, [fixture.original])
  }

  func testRejectsSameNameSameDurationProjectSwitchDuringMetadataQuery() throws {
    let fixture = try InspectionFixture()
    fixture.afterMetadata = .init(library: "Library B", event: "Event", project: "Timeline")
    fixture.changeScope = true
    XCTAssertThrowsError(try fixture.coordinator().inspect(timeout: 2)) { error in
      XCTAssertEqual(
        (error as? FinalCutInspectionError)?.stage, "active-project scope revalidation")
      XCTAssertEqual(
        (error as? FinalCutInspectionError)?.underlying as? FinalCutActionError, .identityMismatch)
    }
    XCTAssertFalse(fixture.calls.contains("status"))
    XCTAssertEqual(fixture.scopesQueried, [fixture.original])
  }

  func testRejectsSameNameProjectMovedToAnotherEventDuringQuery() throws {
    let fixture = try InspectionFixture()
    fixture.afterMetadata = .init(library: "Library A", event: "Another Event", project: "Timeline")
    fixture.changeScope = true
    XCTAssertThrowsError(try fixture.coordinator().inspect(timeout: 2))
  }

  func testRejectsTimelineDisappearingAfterMetadataQuery() throws {
    let fixture = try InspectionFixture()
    fixture.changeScope = true
    XCTAssertThrowsError(try fixture.coordinator().inspect(timeout: 2))
    XCTAssertFalse(fixture.calls.contains("status"))
  }

  func testNoTimelineDoesNotQueryMetadata() throws {
    let fixture = try InspectionFixture()
    fixture.current = nil
    XCTAssertNil(try fixture.coordinator().inspect(timeout: 2))
    XCTAssertEqual(fixture.calls, ["verify", "reveal"])
  }

  func testInitialVerificationFailureKeepsStageAndUnderlyingError() throws {
    let fixture = try InspectionFixture()
    fixture.verificationError = FinalCutAutomationError.notAuthorized
    XCTAssertThrowsError(try fixture.coordinator().inspect(timeout: 2)) { error in
      XCTAssertEqual((error as? FinalCutInspectionError)?.stage, "initial verification")
      XCTAssertEqual(
        (error as? FinalCutInspectionError)?.underlying as? FinalCutAutomationError, .notAuthorized)
    }
    XCTAssertEqual(fixture.calls, ["verify"])
  }

  func testDoesNotRevalidateAfterMetadataExhaustsDeadline() throws {
    let fixture = try InspectionFixture()
    fixture.metadataElapsed = 3
    XCTAssertThrowsError(try fixture.coordinator().inspect(timeout: 2)) { error in
      XCTAssertEqual(
        (error as? FinalCutInspectionError)?.underlying as? FinalCutActionError, .timedOut)
    }
    XCTAssertEqual(fixture.calls, ["verify", "reveal", "metadata"])
  }

  func testRevalidationUsesRemainingBudgetNotOriginalTimeout() throws {
    let fixture = try InspectionFixture()
    fixture.metadataElapsed = 0.75
    _ = try fixture.coordinator().inspect(timeout: 2)
    XCTAssertEqual(fixture.revealTimeouts.count, 2)
    XCTAssertEqual(fixture.revealTimeouts.last ?? -1, 1.25, accuracy: 0.000_001)
  }

  func testRejectsAmbiguousOrWrongScopeMetadataWithoutSecondReveal() throws {
    for records in [
      [NativeProjectRecord](),
      [InspectionFixture.record, InspectionFixture.record],
      [
        NativeProjectRecord(
          library: "Other", event: "Event", project: "Timeline", id: "other", duration: 8,
          frameDuration: 1.0 / 30)
      ],
    ] {
      let fixture = try InspectionFixture()
      fixture.records = records
      XCTAssertThrowsError(try fixture.coordinator().inspect(timeout: 2))
      XCTAssertEqual(fixture.calls, ["verify", "reveal", "metadata"])
    }
  }
}

private final class InspectionFixture {
  static let record = NativeProjectRecord(
    library: "Library A", event: "Event", project: "Timeline", id: "project-id", duration: 8,
    frameDuration: 1.0 / 30)
  let original = FinalCutProjectLocation(library: "Library A", event: "Event", project: "Timeline")
  var current: FinalCutProjectLocation?
  var afterMetadata: FinalCutProjectLocation?
  var changeScope = false
  var calls: [String] = []
  var scopesQueried: [FinalCutProjectLocation] = []
  var revealTimeouts: [TimeInterval] = []
  var now: TimeInterval = 100
  var metadataElapsed: TimeInterval = 0
  var verificationError: Error?
  var records = [InspectionFixture.record]
  let status: LiveTimelineStatus

  init() throws {
    current = original
    status = LiveTimelineStatus(
      project: "Timeline", hours: 0, minutes: 0, seconds: 8, frames: 0,
      timebase: try XCTUnwrap(FinalCutTimebase(formatDescription: "1080p HD 30p, Stereo")))
  }

  func coordinator() -> FinalCutInspectionCoordinator {
    FinalCutInspectionCoordinator(
      verify: { _ in
        self.calls.append("verify")
        if let error = self.verificationError { throw error }
        return 123
      },
      reveal: { pid, timeout in
        XCTAssertEqual(pid, 123)
        self.calls.append("reveal")
        self.revealTimeouts.append(timeout)
        return self.current
      },
      readRecords: { pid, location, _ in
        XCTAssertEqual(pid, 123)
        self.calls.append("metadata")
        self.scopesQueried.append(location)
        self.now += self.metadataElapsed
        if self.changeScope { self.current = self.afterMetadata }
        return self.records
      },
      readStatus: { pid, _ in
        XCTAssertEqual(pid, 123)
        self.calls.append("status")
        return self.status
      },
      clock: { self.now }
    )
  }
}

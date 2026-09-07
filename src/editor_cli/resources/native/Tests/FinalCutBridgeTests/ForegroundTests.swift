import XCTest

@testable import FinalCutBridge

private final class ForegroundStub {
  var now: TimeInterval
  var activations = 0
  var frontmostRaises = 0
  private var windowCounts: [Int]
  private let activates: Bool
  private let raisesFrontmost: Bool

  init(
    windowCounts: [Int], activates: Bool = true, raisesFrontmost: Bool = true,
    now: TimeInterval = 0
  ) {
    self.windowCounts = windowCounts
    self.activates = activates
    self.raisesFrontmost = raisesFrontmost
    self.now = now
  }

  var foreground: FinalCutForeground {
    FinalCutForeground(
      activate: { [self] _ in
        activations += 1
        return activates
      },
      raiseFrontmost: { [self] _ in
        frontmostRaises += 1
        return raisesFrontmost
      },
      windowCount: { [self] _ in windowCounts.isEmpty ? 0 : windowCounts.removeFirst() },
      clock: { [self] in now },
      sleep: { [self] in now += $0 }
    )
  }
}

final class ForegroundTests: XCTestCase {
  func testReturnsWithoutActivatingWhenWindowsAlreadyPublished() throws {
    let stub = ForegroundStub(windowCounts: [2])

    try stub.foreground.raise(processIdentifier: 42, deadline: 5)

    XCTAssertEqual(stub.activations, 0)
  }

  func testActivatesAndWaitsUntilFinalCutPublishesAWindow() throws {
    let stub = ForegroundStub(windowCounts: [0, 0, 0, 1])

    try stub.foreground.raise(processIdentifier: 42, deadline: 5)

    XCTAssertEqual(stub.activations, 1)
    XCTAssertEqual(stub.now, 2 * FinalCutForeground.pollInterval)
  }

  func testFallsBackToTheAccessibilityRaiseWhenActivationIsRefused() throws {
    let stub = ForegroundStub(windowCounts: [0, 0, 1], activates: false)

    try stub.foreground.raise(processIdentifier: 42, deadline: 5)

    XCTAssertEqual(stub.frontmostRaises, 1)
  }

  func testFailsClosedWhenBothRoutesAreRefused() {
    let stub = ForegroundStub(windowCounts: [0], activates: false, raisesFrontmost: false)

    XCTAssertThrowsError(try stub.foreground.raise(processIdentifier: 42, deadline: 5)) { error in
      XCTAssertEqual(error as? FinalCutActionError, .finalCutNotForeground)
    }
  }

  func testRaisesFrontmostWhenCooperativeActivationNeverLands() {
    let stub = ForegroundStub(windowCounts: Array(repeating: 0, count: 400))

    XCTAssertThrowsError(try stub.foreground.raise(processIdentifier: 42, deadline: 3)) { error in
      XCTAssertEqual(error as? FinalCutActionError, .finalCutNotForeground)
    }
    XCTAssertEqual(stub.activations, 1)
    XCTAssertEqual(stub.frontmostRaises, 1)
  }

  func testReportsMissingForegroundRatherThanADeadlineTimeout() {
    let stub = ForegroundStub(windowCounts: Array(repeating: 0, count: 200))

    XCTAssertThrowsError(try stub.foreground.raise(processIdentifier: 42, deadline: 0.2)) { error in
      XCTAssertEqual(error as? FinalCutActionError, .finalCutNotForeground)
    }
    XCTAssertGreaterThanOrEqual(stub.now, 0.2)
  }

  func testRefusesToActivateAfterTheDeadlineHasPassed() {
    let stub = ForegroundStub(windowCounts: [0], now: 3)

    XCTAssertThrowsError(try stub.foreground.raise(processIdentifier: 42, deadline: 1)) { error in
      XCTAssertEqual(error as? FinalCutActionError, .finalCutNotForeground)
    }
    XCTAssertEqual(stub.activations, 0)
  }
}

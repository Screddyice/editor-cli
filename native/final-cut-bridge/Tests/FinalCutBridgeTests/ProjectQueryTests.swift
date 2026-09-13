import AppKit
import Carbon
import Foundation
import XCTest

@testable import FinalCutBridge

final class ProjectQueryTests: XCTestCase {
  func testDecodesNativeMediaTimesAndProjectIDs() throws {
    let result = try decode(list([record()]))
    XCTAssertEqual(
      result,
      [
        NativeProjectRecord(
          library: "Test Library", event: "Test Event", project: "Timeline", id: "project-id",
          duration: 8, frameDuration: 1.0 / 30.0
        )
      ])
  }

  func testPreservesSameNameProjectsWithDifferentIDsForCallerAmbiguityCheck() throws {
    let result = try decode(list([record(id: "first"), record(id: "second")]))
    XCTAssertEqual(result.count, 2)
    XCTAssertEqual(result.map(\.id), ["first", "second"])
  }

  func testAcceptsEmptyScopeAndZeroDuration() throws {
    XCTAssertEqual(try decode(list([])), [])
    XCTAssertEqual(try decode(list([record(duration: time(value: .int32(0)))]))[0].duration, 0)
  }

  func testRejectsDuplicateIDsEvenWithDifferentProjectNames() throws {
    XCTAssertThrowsError(try decode(list([record(), record(project: "Other")])))
  }

  func testRejectsOtherScopesAndCoercedText() throws {
    for candidate in [
      record(library: "Other Library"), record(event: "Other Event"),
      record(library: "test library"), record(project: ""), record(id: ""),
      list([
        .init(string: "Test Library"), .init(string: "Test Event"), .int32(123),
        .init(string: "id"), time(), time(),
      ]),
    ] {
      XCTAssertThrowsError(try decode(list([candidate])))
    }
  }

  func testRejectsMalformedDescriptorStructure() throws {
    for candidate in [
      NSAppleEventDescriptor.null(), .init(string: "[]"), .record(),
      list([.init(string: "record")]), list([list([])]),
      list([list([.init(string: "Test Library")])]),
    ] {
      XCTAssertThrowsError(try decode(candidate))
    }
  }

  func testRejectsInvalidMediaTimes() throws {
    let badTimes = [
      NSAppleEventDescriptor.null(), .init(string: "8s"), list([]),
      time(value: .init(string: "122880")), time(value: .init(boolean: true)),
      time(value: .init(double: .nan)), time(value: .init(double: .infinity)),
      time(value: .init(double: -1)), time(value: .init(double: 0.5)),
      time(value: .init(double: 9_007_199_254_740_992)),
      time(timescale: .int32(0)), time(timescale: .int32(-1)),
      time(timescale: .init(double: 1.5)), time(timescale: .init(string: "15360")),
      time(flags: .int32(0)), time(flags: .int32(5)), time(flags: .int32(9)),
      time(flags: .int32(17)), time(flags: .int32(-1)), time(flags: .init(boolean: true)),
    ]
    for value in badTimes {
      XCTAssertThrowsError(try decode(list([record(duration: value)])))
      XCTAssertThrowsError(try decode(list([record(frame: value)])))
    }
    XCTAssertThrowsError(try decode(list([record(frame: time(value: .int32(0)))])))
  }

  func testAcceptsRoundedValidTimesAndSigned64Values() throws {
    var ticks: Int64 = 122880
    let value = try XCTUnwrap(
      NSAppleEventDescriptor(
        descriptorType: DescType(typeSInt64), bytes: &ticks, length: MemoryLayout<Int64>.size
      ))
    XCTAssertEqual(
      try decode(list([record(duration: time(value: value, flags: .int32(3)))]))[0].duration, 8)
  }

  func testRejectsMalformedNumericDescriptorBytes() throws {
    for kind in [
      DescType(typeSInt16), DescType(typeSInt32), DescType(typeSInt64),
      DescType(typeUInt32), DescType(typeUInt64), DescType(typeIEEE32BitFloatingPoint),
      DescType(typeIEEE64BitFloatingPoint),
    ] {
      let malformed = try XCTUnwrap(NSAppleEventDescriptor(descriptorType: kind, data: Data([0])))
      XCTAssertThrowsError(try decode(list([record(duration: time(value: malformed))])))
    }
  }

  func testFixedQueryCompilesWhenFinalCutDictionaryIsInstalled() throws {
    guard NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.FinalCutApp") != nil
    else {
      throw XCTSkip("Final Cut scripting dictionary is not installed")
    }
    let script = try XCTUnwrap(NSAppleScript(source: TargetedFinalCutProjectReader.source))
    var error: NSDictionary?
    XCTAssertTrue(script.compileAndReturnError(&error), "\(String(describing: error))")
  }

  func testRejectsOversizedResultsAndNames() throws {
    XCTAssertThrowsError(try decode(list((0...4096).map { record(id: "id-\($0)") })))
    XCTAssertThrowsError(try decode(list([record(project: String(repeating: "x", count: 4097))])))
  }

  func testRunArgumentsRoundTripWithoutSourceInterpolation() throws {
    let library = "Library \"\\\nend run\nerror \"injected\"\n--雪"
    let event = "Event \" & (do shell script \"false\") & \""
    let invocation = try TargetedFinalCutProjectReader.runEvent(
      library: library, event: event, timeout: 2
    )
    let script = try XCTUnwrap(NSAppleScript(source: "on run argv\nreturn argv\nend run"))
    var error: NSDictionary?
    let reply = script.executeAppleEvent(invocation, error: &error)
    XCTAssertNil(error)
    XCTAssertEqual(reply.descriptorType, DescType(typeAEList))
    XCTAssertEqual(reply.atIndex(1)?.stringValue, library)
    XCTAssertEqual(reply.atIndex(2)?.stringValue, event)
    XCTAssertEqual(reply.atIndex(3)?.int32Value, 2)
  }

  func testRejectsInvalidArgumentsBeforeDispatch() throws {
    for timeout in [0.0, -1, .nan, .infinity, 3601] {
      XCTAssertThrowsError(
        try TargetedFinalCutProjectReader.runEvent(
          library: "Test Library", event: "Test Event", timeout: timeout
        ))
    }
    for invalid in ["", "\u{0000}", String(repeating: "x", count: 4097)] {
      XCTAssertThrowsError(
        try TargetedFinalCutProjectReader.runEvent(
          library: invalid, event: "Test Event", timeout: 1
        ))
      XCTAssertThrowsError(
        try TargetedFinalCutProjectReader.runEvent(
          library: "Test Library", event: invalid, timeout: 1
        ))
    }
  }

  private func decode(_ descriptor: NSAppleEventDescriptor) throws -> [NativeProjectRecord] {
    try TargetedFinalCutProjectReader.decode(
      descriptor, library: "Test Library", event: "Test Event"
    )
  }

  private func record(
    library: String = "Test Library", event: String = "Test Event", project: String = "Timeline",
    id: String = "project-id", duration: NSAppleEventDescriptor? = nil,
    frame: NSAppleEventDescriptor? = nil
  ) -> NSAppleEventDescriptor {
    list([
      .init(string: library), .init(string: event), .init(string: project), .init(string: id),
      duration ?? time(), frame ?? time(value: .int32(512)),
    ])
  }

  private func time(
    value: NSAppleEventDescriptor = .int32(122880),
    timescale: NSAppleEventDescriptor = .int32(15360),
    flags: NSAppleEventDescriptor = .int32(1)
  ) -> NSAppleEventDescriptor {
    list([value, timescale, flags])
  }

  private func list(_ values: [NSAppleEventDescriptor]) -> NSAppleEventDescriptor {
    let result = NSAppleEventDescriptor.list()
    for (offset, value) in values.enumerated() { result.insert(value, at: offset + 1) }
    return result
  }
}

extension NSAppleEventDescriptor {
  fileprivate static func int32(_ value: Int32) -> NSAppleEventDescriptor { .init(int32: value) }
}

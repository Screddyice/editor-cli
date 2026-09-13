import AppKit
import Carbon
import Foundation

struct NativeProjectRecord: Equatable, Sendable {
  let library: String
  let event: String
  let project: String
  let id: String
  let duration: TimeInterval
  let frameDuration: TimeInterval
}

/// Reads only the named library/event scope. It never changes Final Cut UI or media.
struct TargetedFinalCutProjectReader {
  private static let maximumRecords = 4096
  private static let maximumReplyBytes = 1_048_576
  private static let maximumTextBytes = 4096

  func records(
    processIdentifier: pid_t, library: String, event: String, timeout: TimeInterval
  ) throws -> [NativeProjectRecord] {
    let invocation = try Self.runEvent(library: library, event: event, timeout: timeout)
    let started = ProcessInfo.processInfo.systemUptime
    let budget = min(5, timeout)
    guard
      NSRunningApplication(processIdentifier: processIdentifier)?.bundleIdentifier
        == FinalCutProbe<LiveFinalCutSystem>.bundleIdentifier
    else { throw FinalCutProbeError.wrongBundleIdentifier }
    let target = NSAppleEventDescriptor(processIdentifier: processIdentifier)
    guard let targetDescriptor = target.aeDesc else {
      throw FinalCutAutomationError.invalidTarget
    }
    guard
      AEDeterminePermissionToAutomateTarget(
        targetDescriptor, AEEventClass(kAECoreSuite), AEEventID(kAEGetData), false
      ) == noErr
    else { throw FinalCutAutomationError.notAuthorized }

    let completion = DispatchSemaphore(value: 0)
    let result = ProjectQueryResult()
    DispatchQueue.global(qos: .userInitiated).async {
      defer { completion.signal() }
      guard let script = NSAppleScript(source: Self.source) else {
        result.store(.failure(.invalidReply))
        return
      }
      var error: NSDictionary?
      let reply = script.executeAppleEvent(invocation, error: &error)
      guard error == nil else {
        result.store(.failure(.eventFailed))
        return
      }
      do {
        result.store(.success(try Self.decode(reply, library: library, event: event)))
      } catch {
        result.store(.failure(.invalidReply))
      }
    }
    let remaining = budget - (ProcessInfo.processInfo.systemUptime - started)
    guard remaining > 0, completion.wait(timeout: .now() + remaining) == .success,
      ProcessInfo.processInfo.systemUptime - started < budget
    else {
      throw FinalCutAutomationError.eventFailed
    }
    return try result.load().get()
  }

  static func runEvent(
    library: String, event: String, timeout: TimeInterval
  ) throws -> NSAppleEventDescriptor {
    guard validText(library), validText(event), timeout.isFinite, timeout > 0, timeout <= 3600
    else {
      throw FinalCutAutomationError.invalidTarget
    }
    let arguments = NSAppleEventDescriptor.list()
    arguments.insert(.init(string: library), at: 1)
    arguments.insert(.init(string: event), at: 2)
    arguments.insert(.init(int32: Int32(ceil(min(5, timeout)))), at: 3)
    arguments.insert(.init(int32: Int32(maximumRecords)), at: 4)
    let invocation = NSAppleEventDescriptor(
      eventClass: AEEventClass(kCoreEventClass), eventID: AEEventID(kAEOpenApplication),
      targetDescriptor: nil, returnID: AEReturnID(kAutoGenerateReturnID),
      transactionID: AETransactionID(kAnyTransactionID)
    )
    invocation.setParam(arguments, forKeyword: AEKeyword(keyDirectObject))
    return invocation
  }

  static func decode(
    _ descriptor: NSAppleEventDescriptor, library: String, event: String
  ) throws -> [NativeProjectRecord] {
    guard validText(library), validText(event),
      descriptor.descriptorType == DescType(typeAEList),
      descriptor.numberOfItems <= maximumRecords,
      descriptor.data.count <= maximumReplyBytes
    else { throw FinalCutAutomationError.invalidReply }
    var records: [NativeProjectRecord] = []
    var ids: Set<String> = []
    for offset in 0..<descriptor.numberOfItems {
      guard let row = descriptor.atIndex(offset + 1), row.descriptorType == DescType(typeAEList),
        row.numberOfItems == 6
      else { throw FinalCutAutomationError.invalidReply }
      let actualLibrary = try text(row.atIndex(1))
      let actualEvent = try text(row.atIndex(2))
      let project = try text(row.atIndex(3))
      let id = try text(row.atIndex(4))
      guard actualLibrary == library, actualEvent == event, ids.insert(id).inserted else {
        throw FinalCutAutomationError.invalidReply
      }
      let duration = try mediaTime(row.atIndex(5), allowZero: true)
      let frameDuration = try mediaTime(row.atIndex(6), allowZero: false)
      records.append(
        .init(
          library: actualLibrary, event: actualEvent, project: project, id: id,
          duration: duration, frameDuration: frameDuration
        ))
    }
    return records
  }

  private static func validText(_ value: String) -> Bool {
    !value.isEmpty && value.utf8.count <= maximumTextBytes && !value.contains("\u{0000}")
  }

  private static func text(_ descriptor: NSAppleEventDescriptor?) throws -> String {
    guard let descriptor,
      [DescType(typeUnicodeText), DescType(typeUTF8Text), DescType(typeChar)]
        .contains(descriptor.descriptorType),
      descriptor.data.count <= maximumTextBytes * 2,
      let value = descriptor.stringValue, validText(value)
    else { throw FinalCutAutomationError.invalidReply }
    return value
  }

  private static func number(_ descriptor: NSAppleEventDescriptor?) throws -> Double {
    guard let descriptor else { throw FinalCutAutomationError.invalidReply }
    let expectedBytes: Int
    switch descriptor.descriptorType {
    case DescType(typeSInt16): expectedBytes = 2
    case DescType(typeSInt32), DescType(typeUInt32), DescType(typeIEEE32BitFloatingPoint):
      expectedBytes = 4
    case DescType(typeSInt64), DescType(typeUInt64), DescType(typeIEEE64BitFloatingPoint):
      expectedBytes = 8
    default: throw FinalCutAutomationError.invalidReply
    }
    guard descriptor.data.count == expectedBytes else {
      throw FinalCutAutomationError.invalidReply
    }
    let value = descriptor.doubleValue
    guard value.isFinite, value.rounded(.towardZero) == value else {
      throw FinalCutAutomationError.invalidReply
    }
    return value
  }

  private static func mediaTime(
    _ descriptor: NSAppleEventDescriptor?, allowZero: Bool
  ) throws -> TimeInterval {
    guard let descriptor, descriptor.descriptorType == DescType(typeAEList),
      descriptor.numberOfItems == 3
    else { throw FinalCutAutomationError.invalidReply }
    let value = try number(descriptor.atIndex(1))
    let timescale = try number(descriptor.atIndex(2))
    let flags = try number(descriptor.atIndex(3))
    // CMTime flags: valid=1, rounded=2; reject infinities, indefinite and unknown bits.
    guard flags == 1 || flags == 3, timescale > 0, timescale <= Double(Int32.max),
      value >= 0, value <= 9_007_199_254_740_991
    else { throw FinalCutAutomationError.invalidReply }
    let seconds = value / timescale
    guard seconds.isFinite, allowZero || seconds > 0 else {
      throw FinalCutAutomationError.invalidReply
    }
    return seconds
  }

  // Caller names travel only through run-event arguments. Never interpolate them into source.
  // Both a wall-clock budget and Apple-event timeout bound a worker whose caller has timed out.
  static let source = """
    on run argv
      set wantedLibrary to item 1 of argv
      set wantedEvent to item 2 of argv
      set secondsAllowed to item 3 of argv
      set rowLimit to item 4 of argv
      set queryDeadline to (current date) + secondsAllowed
      set resultRows to {}
      with timeout of secondsAllowed seconds
        tell application id "com.apple.FinalCutApp"
          repeat with libraryItem in (every library whose name is wantedLibrary)
            if (current date) >= queryDeadline then error "Project query timed out"
            set libraryName to name of libraryItem
            considering case
              set libraryMatches to libraryName is wantedLibrary
            end considering
            if libraryMatches then
              repeat with eventItem in (every event of libraryItem whose name is wantedEvent)
                if (current date) >= queryDeadline then error "Project query timed out"
                set eventName to name of eventItem
                considering case
                  set eventMatches to eventName is wantedEvent
                end considering
                if eventMatches then
                  repeat with projectItem in (projects of eventItem)
                    if (current date) >= queryDeadline then error "Project query timed out"
                    if (count of resultRows) >= rowLimit then error "Project query exceeds row limit"
                    set projectName to name of projectItem
                    set projectID to id of projectItem
                    set durationInfo to duration of sequence of projectItem
                    set frameInfo to frame duration of sequence of projectItem
                    set durationParts to {value of durationInfo, timescale of durationInfo, flags of durationInfo}
                    set frameParts to {value of frameInfo, timescale of frameInfo, flags of frameInfo}
                    set end of resultRows to {libraryName, eventName, projectName, projectID, durationParts, frameParts}
                  end repeat
                end if
              end repeat
            end if
          end repeat
        end tell
      end timeout
      return resultRows
    end run
    """
}

private final class ProjectQueryResult: @unchecked Sendable {
  private let lock = NSLock()
  private var value: Result<[NativeProjectRecord], FinalCutAutomationError>?

  func store(_ result: Result<[NativeProjectRecord], FinalCutAutomationError>) {
    lock.lock()
    defer { lock.unlock() }
    value = result
  }

  func load() throws -> Result<[NativeProjectRecord], FinalCutAutomationError> {
    lock.lock()
    defer { lock.unlock() }
    guard let value else { throw FinalCutAutomationError.invalidReply }
    return value
  }
}

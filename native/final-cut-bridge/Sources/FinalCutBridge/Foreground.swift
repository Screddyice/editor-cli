import AppKit
import ApplicationServices
import Foundation

/// Final Cut publishes its accessibility window tree only while it is the
/// active application. A helper launched from a terminal runs in the
/// background, reads an empty `AXWindows` list, and times out on every
/// traversal. Each action raises Final Cut first and waits for a real window.
struct FinalCutForeground {
  /// Cooperative activation. macOS lets the active application hand focus over,
  /// and refuses a background process that no one handed it to.
  let activate: (pid_t) -> Bool
  /// The accessibility route, which answers to the Accessibility grant rather
  /// than to activation cooperation.
  let raiseFrontmost: (pid_t) -> Bool
  let windowCount: (pid_t) -> Int
  let clock: () -> TimeInterval
  let sleep: (TimeInterval) -> Void

  static let pollInterval: TimeInterval = 0.05
  static let cooperativeGrace: TimeInterval = 1

  func raise(processIdentifier: pid_t, deadline: TimeInterval) throws {
    if windowCount(processIdentifier) > 0 { return }
    let started = clock()
    guard started < deadline else { throw FinalCutActionError.finalCutNotForeground }
    var raisedFrontmost = !activate(processIdentifier)
    if raisedFrontmost, !raiseFrontmost(processIdentifier) {
      throw FinalCutActionError.finalCutNotForeground
    }
    while true {
      if windowCount(processIdentifier) > 0 { return }
      let now = clock()
      guard now < deadline else { throw FinalCutActionError.finalCutNotForeground }
      if !raisedFrontmost, now - started >= Self.cooperativeGrace {
        raisedFrontmost = true
        guard raiseFrontmost(processIdentifier) else {
          throw FinalCutActionError.finalCutNotForeground
        }
      }
      sleep(min(Self.pollInterval, deadline - now))
    }
  }

  static let live = FinalCutForeground(
    activate: { processIdentifier in
      guard let app = NSRunningApplication(processIdentifier: processIdentifier) else {
        return false
      }
      if app.isActive { return true }
      if #available(macOS 14.0, *) {
        return app.activate()
      }
      return app.activate(options: [])
    },
    raiseFrontmost: { processIdentifier in
      AXUIElementSetAttributeValue(
        AXUIElementCreateApplication(processIdentifier),
        kAXFrontmostAttribute as CFString,
        kCFBooleanTrue
      ) == .success
    },
    windowCount: { processIdentifier in
      var value: CFTypeRef?
      let element = AXUIElementCreateApplication(processIdentifier)
      guard
        AXUIElementCopyAttributeValue(element, kAXWindowsAttribute as CFString, &value) == .success
      else { return 0 }
      return (value as? [AXUIElement])?.count ?? 0
    },
    clock: { ProcessInfo.processInfo.systemUptime },
    sleep: { Thread.sleep(forTimeInterval: $0) }
  )
}

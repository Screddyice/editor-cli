import AppKit
import ApplicationServices
import Foundation

/// Final Cut publishes its accessibility window tree only while it is the
/// active application, and validates every menu command against its key window.
/// A helper launched from a terminal runs in the background: it reads an empty
/// `AXWindows` list, and once the window tree does appear, every document
/// command still reads as disabled because no window is key.
///
/// macOS will not let a background process take activation. Measured on macOS
/// 26 against Final Cut Pro Creator Studio 12.3: `activate()`, `AXFrontmost`,
/// `AXRaise`, `AXMain` and `AXFocused` all report success, publish the window
/// tree, and leave `isActive` false with every File menu command disabled. So
/// each action asks for activation and then requires the real thing, rather
/// than pressing a disabled command and waiting out its deadline.
struct FinalCutForeground {
  /// Cooperative activation. macOS lets the active application hand focus over,
  /// and refuses a background process that no one handed it to.
  let activate: (pid_t) -> Bool
  /// The accessibility route, which answers to the Accessibility grant rather
  /// than to activation cooperation.
  let raiseFrontmost: (pid_t) -> Bool
  let windowCount: (pid_t) -> Int
  /// Whether Final Cut is genuinely the active application. Reading the window
  /// tree does not need this; every menu command does.
  let isActive: (pid_t) -> Bool
  let clock: () -> TimeInterval
  let sleep: (TimeInterval) -> Void

  static let pollInterval: TimeInterval = 0.05
  static let cooperativeGrace: TimeInterval = 1

  func raise(processIdentifier: pid_t, deadline: TimeInterval) throws {
    if ready(processIdentifier) { return }
    let started = clock()
    guard started < deadline else { throw FinalCutActionError.finalCutNotForeground }
    var raisedFrontmost = !activate(processIdentifier)
    if raisedFrontmost, !raiseFrontmost(processIdentifier) {
      throw FinalCutActionError.finalCutNotForeground
    }
    while true {
      if ready(processIdentifier) { return }
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

  private func ready(_ processIdentifier: pid_t) -> Bool {
    windowCount(processIdentifier) > 0 && isActive(processIdentifier)
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
    isActive: { processIdentifier in
      NSRunningApplication(processIdentifier: processIdentifier)?.isActive ?? false
    },
    clock: { ProcessInfo.processInfo.systemUptime },
    sleep: { Thread.sleep(forTimeInterval: $0) }
  )
}

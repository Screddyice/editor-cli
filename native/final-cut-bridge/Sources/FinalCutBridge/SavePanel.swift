import ApplicationServices
import Foundation

/// Creator Studio's save panel gives accessibility no way to choose a folder.
/// A path written into the name field is saved as a literal filename, and
/// confirming that field navigates nowhere. The panel's Go to Folder sheet does
/// accept a path, and one chord is the only way to open it, so the keyboard
/// surface is exactly that chord and nothing else.
struct FinalCutKeyboard {
  let goToFolderChord: () -> Bool

  static let live = FinalCutKeyboard(goToFolderChord: {
    guard let source = CGEventSource(stateID: .hidSystemState) else { return false }
    let command: CGKeyCode = 55
    let shift: CGKeyCode = 56
    let g: CGKeyCode = 5
    // Final Cut ignores a chord carried only by the flags field, so the
    // modifiers are pressed and released as their own events.
    let strokes: [(CGKeyCode, Bool, CGEventFlags)] = [
      (command, true, [.maskCommand]),
      (shift, true, [.maskCommand, .maskShift]),
      (g, true, [.maskCommand, .maskShift]),
      (g, false, [.maskCommand, .maskShift]),
      (shift, false, [.maskCommand]),
      (command, false, []),
    ]
    for (key, isDown, flags) in strokes {
      guard let event = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: isDown)
      else { return false }
      event.flags = flags
      event.post(tap: .cghidEventTap)
      Thread.sleep(forTimeInterval: 0.02)
    }
    return true
  })
}

enum FinalCutSavePanel {
  static let goToSheetIdentifier = "GoToWindow"
  static let goToPathFieldIdentifier = "PathTextField"
  static let whereIdentifier = "where popup"
  static let nameFieldIdentifier = "saveAsNameTextField"

  /// The Where popup shows a display name wrapped in bidirectional isolates,
  /// so compare on the folder name alone.
  static func displays(directory: String, popupValue: String?) -> Bool {
    guard let popupValue else { return false }
    let expected = URL(fileURLWithPath: directory).lastPathComponent
    guard !expected.isEmpty else { return false }
    let cleaned = popupValue.unicodeScalars.filter {
      !(0x2066...0x2069).contains($0.value) && $0.value != 0x200E && $0.value != 0x200F
    }
    return String(String.UnicodeScalarView(cleaned))
      .localizedCaseInsensitiveContains(expected)
  }
}

/// Creator Studio writes a `.fcpxmld` bundle at every XML version it offers,
/// so the exported artifact is a package with the document inside it.
enum FinalCutExportArtifact {
  static let bundleExtension = "fcpxmld"
  static let documentName = "Info.fcpxml"

  /// The path the save panel produces for a requested destination. Final Cut
  /// appends its own extension, so it is given the stem.
  static func bundlePath(for destination: String) -> String {
    let url = URL(fileURLWithPath: destination)
    guard url.pathExtension.lowercased() != bundleExtension else { return destination }
    return url.deletingPathExtension().appendingPathExtension(bundleExtension).path
  }

  static func panelName(for destination: String) -> String {
    URL(fileURLWithPath: bundlePath(for: destination)).deletingPathExtension().lastPathComponent
  }

  /// The readable document for a path: a bundle answers with the FCPXML inside.
  static func readablePath(of path: String) -> String {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory),
      isDirectory.boolValue
    else { return path }
    return URL(fileURLWithPath: path).appendingPathComponent(documentName).path
  }
}

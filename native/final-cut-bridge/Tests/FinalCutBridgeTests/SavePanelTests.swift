import XCTest

@testable import FinalCutBridge

final class SavePanelTests: XCTestCase {
  func testWhereMatchIgnoresTheBidirectionalIsolatesFinalCutWraps() {
    XCTAssertTrue(
      FinalCutSavePanel.displays(
        directory: "/tmp/fcp-session", popupValue: "\u{2066}fcp-session\u{2069}"
      )
    )
    XCTAssertFalse(
      FinalCutSavePanel.displays(
        directory: "/tmp/fcp-session", popupValue: "\u{2066}Desktop\u{2069} — iCloud"
      )
    )
    XCTAssertFalse(
      FinalCutSavePanel.displays(directory: "/tmp/fcp-session", popupValue: nil)
    )
  }

  func testExportArtifactIsTheBundleFinalCutActuallyWrites() {
    XCTAssertEqual(
      FinalCutExportArtifact.bundlePath(for: "/tmp/session/source.fcpxml"),
      "/tmp/session/source.fcpxmld"
    )
    XCTAssertEqual(
      FinalCutExportArtifact.bundlePath(for: "/tmp/session/source.fcpxmld"),
      "/tmp/session/source.fcpxmld"
    )
    XCTAssertEqual(
      FinalCutExportArtifact.panelName(for: "/tmp/session/source.fcpxml"), "source"
    )
  }

  func testReadablePathReachesInsideABundleAndLeavesAFileAlone() throws {
    let root = URL(fileURLWithPath: NSTemporaryDirectory())
      .appendingPathComponent(UUID().uuidString)
    let bundle = root.appendingPathComponent("source.fcpxmld")
    try FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: true)
    let document = bundle.appendingPathComponent(FinalCutExportArtifact.documentName)
    try Data("<fcpxml/>".utf8).write(to: document)
    let flat = root.appendingPathComponent("other.fcpxml")
    try Data("<fcpxml/>".utf8).write(to: flat)
    defer { try? FileManager.default.removeItem(at: root) }

    XCTAssertEqual(FinalCutExportArtifact.readablePath(of: bundle.path), document.path)
    XCTAssertEqual(FinalCutExportArtifact.readablePath(of: flat.path), flat.path)
    XCTAssertEqual(
      FinalCutExportArtifact.readablePath(of: root.appendingPathComponent("missing").path),
      root.appendingPathComponent("missing").path
    )
  }

  func testDirectorySelectionSkipsTheChordWhenThePanelIsAlreadyThere() throws {
    var chords = 0
    let panel = Self.exportPanel(where: "\u{2066}fcp-session\u{2069}")
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: []), panel,
    ])
    let controller = LiveFinalCutAX(
      root: root, keyboard: FinalCutKeyboard(goToFolderChord: { chords += 1; return true })
    )

    try controller.selectSaveDirectory("/tmp/fcp-session", stage: .exportXML, timeout: 1)

    XCTAssertEqual(chords, 0)
  }

  func testDirectorySelectionWritesThePathAndProvesThePanelMoved() throws {
    var chords = 0
    let pathField = FakeFinalCutAXElement(
      role: kAXTextFieldRole as String, identifier: FinalCutSavePanel.goToPathFieldIdentifier
    )
    let goToSheet = FakeFinalCutAXElement(
      role: kAXSheetRole as String, identifier: FinalCutSavePanel.goToSheetIdentifier,
      hidden: true, children: [pathField]
    )
    let wherePopup = FakeFinalCutAXElement(
      role: "AXPopUpButton", identifier: FinalCutSavePanel.whereIdentifier
    )
    wherePopup.setTestAttribute(kAXValueAttribute as String, "\u{2066}Desktop\u{2069} — iCloud")
    let panel = FakeFinalCutAXElement(
      role: kAXWindowRole as String, title: "Export XML", subrole: "AXDialog",
      children: [wherePopup, goToSheet, .textField(), .button("Save")]
    )
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: []), panel,
    ])
    // The chord reveals the sheet; confirming the path moves the panel.
    let keyboard = FinalCutKeyboard(goToFolderChord: {
      chords += 1
      goToSheet.setTestAttribute(kAXHiddenAttribute as String, false)
      return true
    })
    pathField.onConfirm = {
      wherePopup.setTestAttribute(kAXValueAttribute as String, "\u{2066}fcp-session\u{2069}")
    }

    try LiveFinalCutAX(root: root, keyboard: keyboard)
      .selectSaveDirectory("/tmp/fcp-session", stage: .exportXML, timeout: 2)

    XCTAssertEqual(chords, 1)
    XCTAssertEqual(pathField.writtenValue, "/tmp/fcp-session")
    XCTAssertTrue(pathField.confirmed)
  }

  func testDirectorySelectionFailsWhenThePanelNeverMoves() {
    let pathField = FakeFinalCutAXElement(
      role: kAXTextFieldRole as String, identifier: FinalCutSavePanel.goToPathFieldIdentifier
    )
    let goToSheet = FakeFinalCutAXElement(
      role: kAXSheetRole as String, identifier: FinalCutSavePanel.goToSheetIdentifier,
      children: [pathField]
    )
    let wherePopup = FakeFinalCutAXElement(
      role: "AXPopUpButton", identifier: FinalCutSavePanel.whereIdentifier
    )
    wherePopup.setTestAttribute(kAXValueAttribute as String, "\u{2066}Desktop\u{2069} — iCloud")
    let panel = FakeFinalCutAXElement(
      role: kAXWindowRole as String, title: "Export XML", subrole: "AXDialog",
      children: [wherePopup, goToSheet, .textField(), .button("Save")]
    )
    let root = FakeFinalCutAXElement.application(children: [
      .window(title: "Final Cut Pro", children: []), panel,
    ])

    XCTAssertThrowsError(
      try LiveFinalCutAX(
        root: root, keyboard: FinalCutKeyboard(goToFolderChord: { true })
      ).selectSaveDirectory("/tmp/fcp-session", stage: .exportXML, timeout: 0.3)
    ) { error in
      XCTAssertEqual(error as? FinalCutActionError, .timedOut)
    }
  }

  private static func exportPanel(where value: String) -> FakeFinalCutAXElement {
    let wherePopup = FakeFinalCutAXElement(
      role: "AXPopUpButton", identifier: FinalCutSavePanel.whereIdentifier
    )
    wherePopup.setTestAttribute(kAXValueAttribute as String, value)
    return FakeFinalCutAXElement(
      role: kAXWindowRole as String, title: "Export XML", subrole: "AXDialog",
      children: [wherePopup, .textField(), .button("Save")]
    )
  }
}

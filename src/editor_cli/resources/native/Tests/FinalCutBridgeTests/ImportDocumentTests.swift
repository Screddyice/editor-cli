import Foundation
import XCTest
@testable import FinalCutBridge

final class ImportDocumentTests: XCTestCase {
  func testImportCopyBindsTheExactLibraryWithoutChangingTheCandidate() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("candidate.fcpxml")
    let output = root.appendingPathComponent("import.fcpxml")
    let library = root.appendingPathComponent("Canary.fcpbundle")
    let bytes = Data("<fcpxml version=\"1.14\"><resources/><library location=\"\(library.absoluteString)\"><event name=\"Event\"><project name=\"Pass\"/></event></library></fcpxml>".utf8)
    try bytes.write(to: source)
    try FCPXMLImportDocument.prepare(source: source, library: library, destination: output)
    XCTAssertEqual(try Data(contentsOf: source), bytes)
    let document = try XMLDocument(contentsOf: output)
    let option = try XCTUnwrap(document.nodes(forXPath: "/fcpxml/import-options/option[@key='library location']").first as? XMLElement)
    XCTAssertEqual(option.attribute(forName: "value")?.stringValue, library.absoluteString)
    XCTAssertThrowsError(try FCPXMLImportDocument.prepare(source: source, library: library, destination: output))
    XCTAssertThrowsError(try FCPXMLImportDocument.prepare(source: source, library: root.appendingPathComponent("Other.fcpbundle"), destination: root.appendingPathComponent("wrong.fcpxml")))
  }
}

import Foundation
import XCTest
@testable import FinalCutBridge

final class ShareCompletionTests: XCTestCase {
  func testWritesExactDurableReceiptAndRefusesReplacement() throws {
    let root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let output = root.appendingPathComponent("preview.mp4")
    try Data("preview".utf8).write(to: output)
    let identity = ProjectIdentity(library: "Library", event: "Event", project: "Pass 1", duration: 12)

    try ShareCompletion.write(output: output.path, identity: identity)

    let receiptURL = URL(fileURLWithPath: ShareCompletion.receiptPath(for: output.path))
    let receipt = try JSONDecoder().decode(ShareCompletionReceipt.self, from: Data(contentsOf: receiptURL))
    XCTAssertEqual(receipt.version, 1)
    XCTAssertEqual(receipt.kind, "final_cut_share")
    XCTAssertEqual(receipt.identity, identity)
    XCTAssertEqual(receipt.output, output.path)
    XCTAssertEqual(receipt.sha256, "5975cf1bba432391c94667f5886225f69377c0aa8b9fa21fddfb21c89bcf9092")
    XCTAssertEqual(receipt.sizeBytes, 7)
    XCTAssertThrowsError(try ShareCompletion.write(output: output.path, identity: identity))
  }
}

import CryptoKit
import Darwin
import Foundation

struct ShareCompletionReceipt: Codable, Equatable {
  let version: Int
  let kind: String
  let identity: ProjectIdentity
  let output: String
  let sha256: String
  let sizeBytes: UInt64

  enum CodingKeys: String, CodingKey {
    case version, kind, identity, output, sha256
    case sizeBytes = "size_bytes"
  }
}

enum ShareCompletion {
  static func receiptPath(for output: String) -> String {
    output + ".receipt.json"
  }

  static func write(output: String, identity: ProjectIdentity) throws {
    let outputURL = URL(fileURLWithPath: output)
    let attributes = try FileManager.default.attributesOfItem(atPath: output)
    guard let size = attributes[.size] as? NSNumber, size.uint64Value > 0 else {
      throw FinalCutActionError.invalidPath
    }
    let receipt = ShareCompletionReceipt(
      version: 1,
      kind: "final_cut_share",
      identity: identity,
      output: outputURL.standardizedFileURL.path,
      sha256: try digest(outputURL),
      sizeBytes: size.uint64Value
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let bytes = try encoder.encode(receipt)
    let destination = URL(fileURLWithPath: receiptPath(for: output))
    let temporary = destination.deletingLastPathComponent()
      .appendingPathComponent(".share-receipt-\(UUID().uuidString).tmp")
    guard FileManager.default.createFile(atPath: temporary.path, contents: nil) else {
      throw FinalCutActionError.invalidPath
    }
    defer { try? FileManager.default.removeItem(at: temporary) }
    let handle = try FileHandle(forWritingTo: temporary)
    try handle.write(contentsOf: bytes)
    try handle.synchronize()
    try handle.close()
    try FileManager.default.linkItem(at: temporary, to: destination)
    let directory = open(destination.deletingLastPathComponent().path, O_RDONLY)
    guard directory >= 0 else { throw FinalCutActionError.invalidPath }
    defer { close(directory) }
    guard fsync(directory) == 0 else { throw FinalCutActionError.invalidPath }
  }

  private static func digest(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hasher = SHA256()
    while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty {
      hasher.update(data: data)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
  }
}

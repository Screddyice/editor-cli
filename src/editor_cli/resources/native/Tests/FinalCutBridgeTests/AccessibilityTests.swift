import ApplicationServices
import XCTest

@testable import FinalCutBridge

final class AccessibilityTests: XCTestCase {
  func testMenuLookupRejectsAmbiguousItems() throws {
    let root = FakeNode(
      role: kAXApplicationRole as String,
      children: [
        .menu("File", items: [.item("Export XML..."), .item("Export XML...")])
      ])

    XCTAssertThrowsError(
      try BoundedAX(root: root).uniqueMenuItem(path: ["File", "Export XML..."])
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .ambiguousMatch)
    }
  }

  func testMenuLookupReturnsTheOnlyMatchingItem() throws {
    let expected = FakeNode.item("Export XML...")
    let root = FakeNode(
      role: kAXApplicationRole as String,
      children: [
        .menu("File", items: [.item("Open..."), expected])
      ])

    let result = try BoundedAX(root: root).uniqueMenuItem(path: ["File", "Export XML..."])

    XCTAssertEqual(result.title, "Export XML...")
    XCTAssertEqual(result.role, kAXMenuItemRole as String)
  }

  func testMenuLookupDoesNotTraverseDisallowedRoles() throws {
    let root = FakeNode(
      role: kAXApplicationRole as String,
      children: [
        FakeNode(
          role: "AXWebArea",
          children: [.menu("File", items: [.item("Export XML...")])]
        )
      ])

    XCTAssertThrowsError(
      try BoundedAX(root: root).uniqueMenuItem(path: ["File", "Export XML..."])
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .noMatch)
    }
  }

  func testMenuLookupFailsClosedAtDepthLimit() throws {
    let root = FakeNode(
      role: kAXApplicationRole as String,
      children: [
        FakeNode(
          role: kAXWindowRole as String,
          children: [
            .menu("File", items: [.item("Export XML...")])
          ])
      ])

    XCTAssertThrowsError(
      try BoundedAX(
        root: root, limits: .init(maxDepth: 1, maxVisitedNodes: 32, maxChildrenPerNode: 32)
      )
      .uniqueMenuItem(path: ["File", "Export XML..."])
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .traversalLimitExceeded)
    }
  }

  func testMenuLookupFailsClosedAtNodeLimit() throws {
    let root = FakeNode(
      role: kAXApplicationRole as String,
      children: [
        FakeNode(role: kAXWindowRole as String),
        FakeNode(role: kAXWindowRole as String),
      ])

    XCTAssertThrowsError(
      try BoundedAX(
        root: root, limits: .init(maxDepth: 8, maxVisitedNodes: 2, maxChildrenPerNode: 32)
      )
      .uniqueMenuItem(path: ["File"])
    ) { error in
      XCTAssertEqual(error as? AccessibilityDiscoveryError, .traversalLimitExceeded)
    }
  }
}

final class FakeNode: AccessibilityNode {
  let role: String
  let title: String?
  let children: [FakeNode]

  init(role: String, title: String? = nil, children: [FakeNode] = []) {
    self.role = role
    self.title = title
    self.children = children
  }

  static func menu(_ title: String, items: [FakeNode]) -> FakeNode {
    FakeNode(role: kAXMenuRole as String, title: title, children: items)
  }

  static func item(_ title: String) -> FakeNode {
    FakeNode(role: kAXMenuItemRole as String, title: title)
  }

  func accessibilityRole() throws -> String {
    role
  }

  func accessibilityTitle() throws -> String? {
    title
  }

  func accessibilityChildren() throws -> [any AccessibilityNode] {
    children
  }
}

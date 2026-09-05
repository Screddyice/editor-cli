import ApplicationServices
import Foundation

protocol AccessibilityNode {
  func accessibilityRole() throws -> String
  func accessibilityTitle() throws -> String?
  func accessibilityChildren() throws -> [any AccessibilityNode]
}

struct AccessibilityMatch: Equatable {
  let role: String
  let title: String
}

struct AccessibilityTraversalLimits {
  let maxDepth: Int
  let maxVisitedNodes: Int
  let maxChildrenPerNode: Int

  static let `default` = AccessibilityTraversalLimits(
    maxDepth: 12,
    maxVisitedNodes: 2_048,
    maxChildrenPerNode: 256
  )
}

enum AccessibilityDiscoveryError: Error, Equatable {
  case invalidPath
  case noMatch
  case ambiguousMatch
  case unexpectedFinalRole
  case traversalLimitExceeded
  case attributeUnavailable
}

struct BoundedAX {
  let root: any AccessibilityNode
  let limits: AccessibilityTraversalLimits

  private let allowedRoles: Set<String> = [
    kAXApplicationRole as String, kAXMenuBarRole as String,
    kAXMenuBarItemRole as String, kAXMenuRole as String,
    kAXMenuItemRole as String, kAXWindowRole as String,
    kAXSheetRole as String, kAXButtonRole as String,
    kAXTextFieldRole as String, kAXStaticTextRole as String,
    kAXProgressIndicatorRole as String,
  ]

  init(
    root: any AccessibilityNode,
    limits: AccessibilityTraversalLimits = .default
  ) {
    self.root = root
    self.limits = limits
  }

  func uniqueMenuItem(path: [String]) throws -> AccessibilityMatch {
    guard !path.isEmpty, path.allSatisfy({ !$0.isEmpty }) else {
      throw AccessibilityDiscoveryError.invalidPath
    }

    var visitedNodes = 0
    var searchRoots: [any AccessibilityNode] = [root]
    var match: (any AccessibilityNode)?

    for title in path {
      var matches: [any AccessibilityNode] = []
      for searchRoot in searchRoots {
        try collectFirstMatches(
          titled: title,
          beneath: searchRoot,
          depth: 0,
          visitedNodes: &visitedNodes,
          matches: &matches
        )
      }

      guard !matches.isEmpty else {
        throw AccessibilityDiscoveryError.noMatch
      }
      guard matches.count == 1 else {
        throw AccessibilityDiscoveryError.ambiguousMatch
      }
      match = matches[0]
      searchRoots = try matches[0].accessibilityChildren()
      guard searchRoots.count <= limits.maxChildrenPerNode else {
        throw AccessibilityDiscoveryError.traversalLimitExceeded
      }
    }

    guard let match else {
      throw AccessibilityDiscoveryError.noMatch
    }
    let role = try match.accessibilityRole()
    guard role == kAXMenuItemRole as String else {
      throw AccessibilityDiscoveryError.unexpectedFinalRole
    }
    guard let title = try match.accessibilityTitle() else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return AccessibilityMatch(role: role, title: title)
  }

  private func collectFirstMatches(
    titled title: String,
    beneath node: any AccessibilityNode,
    depth: Int,
    visitedNodes: inout Int,
    matches: inout [any AccessibilityNode]
  ) throws {
    guard depth <= limits.maxDepth else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }

    visitedNodes += 1
    guard visitedNodes <= limits.maxVisitedNodes else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }

    let role = try node.accessibilityRole()
    guard allowedRoles.contains(role) else {
      return
    }

    if try node.accessibilityTitle() == title {
      matches.append(node)
      return
    }

    let children = try node.accessibilityChildren()
    guard children.count <= limits.maxChildrenPerNode else {
      throw AccessibilityDiscoveryError.traversalLimitExceeded
    }
    for child in children {
      try collectFirstMatches(
        titled: title,
        beneath: child,
        depth: depth + 1,
        visitedNodes: &visitedNodes,
        matches: &matches
      )
    }
  }
}

final class LiveAccessibilityNode: AccessibilityNode {
  private let element: AXUIElement

  init(element: AXUIElement) {
    self.element = element
  }

  static func application(processIdentifier: pid_t) -> LiveAccessibilityNode {
    LiveAccessibilityNode(element: AXUIElementCreateApplication(processIdentifier))
  }

  func accessibilityRole() throws -> String {
    guard let role = try attribute(kAXRoleAttribute as String) as? String else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return role
  }

  func accessibilityTitle() throws -> String? {
    do {
      return try attribute(kAXTitleAttribute as String) as? String
    } catch AccessibilityDiscoveryError.attributeUnavailable {
      return nil
    }
  }

  func accessibilityChildren() throws -> [any AccessibilityNode] {
    do {
      guard let elements = try attribute(kAXChildrenAttribute as String) as? [AXUIElement] else {
        return []
      }
      return elements.map(LiveAccessibilityNode.init(element:))
    } catch AccessibilityDiscoveryError.attributeUnavailable {
      return []
    }
  }

  private func attribute(_ name: String) throws -> AnyObject? {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    guard status == .success else {
      throw AccessibilityDiscoveryError.attributeUnavailable
    }
    return value
  }
}

import Foundation

/// An import copy targets the library proven by Final Cut's sidebar, while the
/// candidate document remains the immutable input to review and recovery.
enum FCPXMLImportDocument {
  static func prepare(source: URL, library: URL, destination: URL) throws {
    guard !FileManager.default.fileExists(atPath: destination.path) else {
      throw FinalCutActionError.outputAlreadyExists
    }
    let document = try XMLDocument(contentsOf: source, options: [.nodeLoadExternalEntitiesNever])
    guard let root = document.rootElement(), root.name == "fcpxml" else {
      throw FinalCutActionError.invalidPath
    }
    let libraries = try document.nodes(forXPath: "/fcpxml/library")
    guard libraries.count == 1, let declared = libraries.first as? XMLElement else {
      throw FinalCutActionError.invalidPath
    }
    if let value = declared.attribute(forName: "location")?.stringValue {
      guard let location = URL(string: value), location.isFileURL,
        location.standardizedFileURL.path == library.standardizedFileURL.path
      else { throw FinalCutActionError.identityMismatch }
    }
    let projects = try document.nodes(forXPath: "/fcpxml/library/event/project")
    guard projects.count == 1, let project = projects.first as? XMLElement else {
      throw FinalCutActionError.invalidIdentity
    }
    project.removeAttribute(forName: "uid")
    project.removeAttribute(forName: "modDate")
    for old in root.elements(forName: "import-options") { old.detach() }
    let options = XMLElement(name: "import-options")
    for (key, value) in [("library location", library.absoluteString), ("copy assets", "0"), ("suppress warnings", "0")] {
      let option = XMLElement(name: "option")
      option.addAttribute(XMLNode.attribute(withName: "key", stringValue: key) as! XMLNode)
      option.addAttribute(XMLNode.attribute(withName: "value", stringValue: value) as! XMLNode)
      options.addChild(option)
    }
    root.insertChild(options, at: 0)
    try document.xmlData.write(to: destination, options: .withoutOverwriting)
  }
}

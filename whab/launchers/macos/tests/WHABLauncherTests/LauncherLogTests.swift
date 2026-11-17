import Foundation
import Darwin
import XCTest
@testable import WHABLauncher

final class LauncherLogTests: XCTestCase {
    private func logRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(at: root.appendingPathComponent("logs"), withIntermediateDirectories: true)
        addTeardownBlock { try FileManager.default.removeItem(at: root) }
        return root
    }

    func testLogsHaveFixedLocalPathsAndExplicitTitles() throws {
        let root = try logRoot()
        XCTAssertEqual(LauncherLog.allCases.map(\.title), ["WHAB", "Installation", "Sandbox startup", "Launcher manager"])
        XCTAssertNil(LauncherLog(rawValue: "../../private"))
        for log in LauncherLog.allCases {
            XCTAssertEqual(log.fileURL(root: root).deletingLastPathComponent(), root.appendingPathComponent("logs"))
            XCTAssertEqual(log.fileURL(root: root).lastPathComponent, log.rawValue + ".log")
        }
    }

    func testLogReadShowsFictionalStartupTextAndRefreshesWithoutModification() throws {
        let root = try logRoot()
        let file = LauncherLog.whab.fileURL(root: root)
        let first = "Fictional startup failed: display selection unavailable.\nEmoji: 🍏\n"
        try Data(first.utf8).write(to: file)
        XCTAssertEqual(LauncherLog.whab.read(root: root), first)
        let second = first + "Fictional startup recovered.\n"
        try Data(second.utf8).write(to: file)
        XCTAssertEqual(LauncherLog.whab.read(root: root), second)
        XCTAssertEqual(try Data(contentsOf: file), Data(second.utf8))
        let installation = LauncherLog.install.fileURL(root: root)
        try Data("Installed a fictional build.\n".utf8).write(to: installation)
        XCTAssertEqual(LauncherLog.whab.read(root: root), second, "New installation logs must not replace the selected startup log.")
        XCTAssertEqual(LauncherLog.install.read(root: root), "Installed a fictional build.\n")
    }

    func testLargeLogsReadOnlyTheBoundedTailWithACompleteLastLine() throws {
        let root = try logRoot()
        let file = LauncherLog.whab.fileURL(root: root)
        let content = String(repeating: "old fictional entry 🍏\n", count: 20) + "latest fictional failure\n"
        try Data(content.utf8).write(to: file)
        let displayed = LauncherLog.whab.read(root: root, limit: 53)
        XCTAssertTrue(displayed.hasPrefix("[Showing the latest 53 bytes."))
        XCTAssertTrue(displayed.hasSuffix("latest fictional failure\n"))
        XCTAssertFalse(displayed.contains("�"))
        XCTAssertLessThan(displayed.utf8.count, content.utf8.count)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), content)
    }

    func testMissingEmptyOrNonregularLogsHaveReadableStates() throws {
        let root = try logRoot()
        XCTAssertTrue(LauncherLog.whab.read(root: root).contains("not available yet"))
        let file = LauncherLog.whab.fileURL(root: root)
        try Data().write(to: file)
        XCTAssertTrue(LauncherLog.whab.read(root: root).contains("empty"))
        try FileManager.default.removeItem(at: file)
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        XCTAssertTrue(LauncherLog.whab.read(root: root).contains("not a regular local file"))
    }

    func testLogSymlinksCannotReadAnUnrelatedPrivateFile() throws {
        let root = try logRoot()
        let privateFile = root.appendingPathComponent("private.txt")
        try Data("fictional private value".utf8).write(to: privateFile)
        try FileManager.default.createSymbolicLink(at: LauncherLog.whab.fileURL(root: root), withDestinationURL: privateFile)
        let displayed = LauncherLog.whab.read(root: root)
        XCTAssertTrue(displayed.contains("not a regular local file"))
        XCTAssertFalse(displayed.contains("fictional private value"))
    }

    func testLogDirectorySymlinksCannotReadAnotherFolder() throws {
        let root = try logRoot()
        let other = root.appendingPathComponent("other")
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: false)
        try Data("fictional private value".utf8).write(to: other.appendingPathComponent("whab.log"))
        let logs = root.appendingPathComponent("logs")
        try FileManager.default.removeItem(at: logs)
        try FileManager.default.createSymbolicLink(at: logs, withDestinationURL: other)
        XCTAssertTrue(LauncherLog.whab.read(root: root).contains("not a regular local file"))
    }

    func testNamedPipeCannotBlockTheLogWindow() throws {
        let root = try logRoot()
        let file = LauncherLog.whab.fileURL(root: root)
        XCTAssertEqual(mkfifo(file.path, 0o600), 0)
        XCTAssertTrue(LauncherLog.whab.read(root: root).contains("not a regular local file"))
    }
}

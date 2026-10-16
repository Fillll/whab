import Foundation
import XCTest
@testable import WHABLauncher

final class LauncherModelTests: XCTestCase {
    func testDecodesManagerEnvelopeAndConfinesBrowserToLocalHTTPS() throws {
        let data = Data("""
            {"ok":true,"state":"running","running":true,"url":"https://127.0.0.1:8765/",
             "runtime_home":"/private/runtime","update_available":true,"channel":"main"}
            """.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.displayState, "Running")
        XCTAssertEqual(reply.runtimeHome, "/private/runtime")
        XCTAssertEqual(reply.localURL?.host, "127.0.0.1")
        XCTAssertEqual(reply.updateAvailable, true)
        for url in ["http://localhost:8765", "https://example.com", "https://localhost.evil.test", "file:///etc/passwd", "https://user:password@localhost"] {
            var modified = reply
            modified.url = url
            XCTAssertNil(modified.localURL, url)
        }
    }

    func testPathsWithSpacesRemainSeparateProcessArguments() {
        let invocation = ManagerInvocation(resources: URL(fileURLWithPath: "/Applications/WHAB App.app/Contents/Resources"),
                                           root: URL(fileURLWithPath: "/Users/Example/Library/Application Support/WHAB/launcher"),
                                           command: "configure", options: ["--runtime-home", "/Users/Example/My Budget"])
        XCTAssertEqual(invocation.executable.lastPathComponent, "python3.14")
        XCTAssertEqual(Array(invocation.arguments.prefix(2)), ["-I", "-B"])
        XCTAssertEqual(invocation.arguments.suffix(3), ["configure", "--runtime-home", "/Users/Example/My Budget"])
        XCTAssertTrue(invocation.arguments.contains("/Users/Example/Library/Application Support/WHAB/launcher"))
    }

    func testErrorResponseIsReadable() throws {
        let data = Data(#"{"ok":false,"error":{"code":"busy","message":"WHAB is already updating."}}"#.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertFalse(reply.ok)
        XCTAssertEqual(reply.error?.message, "WHAB is already updating.")
    }
}

import Foundation
import XCTest
@testable import WHABLauncher

final class LauncherModelTests: XCTestCase {
    func testMenuWaitsForInitialStatusBeforeAllowingAnAction() {
        let state = LauncherMenuState(reply: nil)
        XCTAssertEqual(state.title, "Checking…")
        XCTAssertEqual(state.indicator, .working)
        XCTAssertFalse(state.isEnabled)
        XCTAssertFalse(state.opensSettings)
    }

    func testMenuShowsForegroundActivityInsteadOfAnEarlierOnlineReply() throws {
        let reply = try JSONDecoder().decode(ManagerReply.self, from: Data(
            #"{"ok":true,"state":"running","running":true,"url":"https://localhost:5173/"}"#.utf8))
        let state = LauncherMenuState(reply: reply, activity: "Preparing update…")
        XCTAssertEqual(state.title, "Preparing update…")
        XCTAssertEqual(state.indicator, .working)
        XCTAssertFalse(state.isEnabled)
        XCTAssertFalse(state.opensSettings)
    }

    func testMenuOpensTheBrowserForAnOnlineLocalHTTPSRuntime() throws {
        let reply = try JSONDecoder().decode(ManagerReply.self, from: Data(
            #"{"ok":true,"state":"running","running":true,"url":"https://localhost:5173/"}"#.utf8))
        let state = LauncherMenuState(reply: reply)
        XCTAssertEqual(state.title, "Online — Open")
        XCTAssertEqual(state.indicator, .online)
        XCTAssertTrue(state.isEnabled)
        XCTAssertFalse(state.opensSettings)
    }

    func testMenuOffersStartWhenTheConfiguredRuntimeIsStopped() throws {
        let reply = try JSONDecoder().decode(ManagerReply.self, from: Data(
            #"{"ok":true,"state":"stopped","running":false,"url":"https://localhost:5173/"}"#.utf8))
        let state = LauncherMenuState(reply: reply)
        XCTAssertEqual(state.title, "Offline — Start")
        XCTAssertEqual(state.indicator, .offline)
        XCTAssertTrue(state.isEnabled)
        XCTAssertFalse(state.opensSettings)
    }

    func testMenuRoutesUnconfiguredRuntimeToSettings() throws {
        let reply = try JSONDecoder().decode(ManagerReply.self, from: Data(
            #"{"ok":true,"state":"unconfigured","running":false}"#.utf8))
        let state = LauncherMenuState(reply: reply)
        XCTAssertEqual(state.title, "Setup needed — Settings…")
        XCTAssertEqual(state.indicator, .offline)
        XCTAssertTrue(state.isEnabled)
        XCTAssertTrue(state.opensSettings)
    }

    func testMenuRoutesManagerFailureToSettingsDespiteAnEarlierOnlineReply() throws {
        let reply = try JSONDecoder().decode(ManagerReply.self, from: Data(
            #"{"ok":true,"state":"running","running":true,"url":"https://localhost:5173/"}"#.utf8))
        let state = LauncherMenuState(reply: reply, needsAttention: true)
        XCTAssertEqual(state.title, "Needs attention — Settings…")
        XCTAssertEqual(state.indicator, .attention)
        XCTAssertTrue(state.isEnabled)
        XCTAssertTrue(state.opensSettings)
    }

    func testMenuRequiresAnAllowedBrowserURLBeforeShowingOnline() throws {
        var reply = try JSONDecoder().decode(ManagerReply.self, from: Data(
            #"{"ok":true,"state":"running","running":true}"#.utf8))
        for url in [nil, "http://localhost:5173/", "https://example.com/", "https://user:password@localhost/"] as [String?] {
            reply.url = url
            let state = LauncherMenuState(reply: reply)
            XCTAssertEqual(state.title, "Unavailable — Settings…")
            XCTAssertEqual(state.indicator, .attention)
            XCTAssertTrue(state.isEnabled)
            XCTAssertTrue(state.opensSettings)
        }
    }

    func testUpdateChannelsHaveExplicitStableMappings() {
        XCTAssertEqual(LauncherChannel.allCases.map(\.rawValue), ["main", "stable", "alpha"])
        XCTAssertEqual(Set(LauncherChannel.allCases.map(\.title)).count, 3)
        for channel in LauncherChannel.allCases {
            XCTAssertEqual(LauncherChannel.configured(channel.rawValue), channel)
        }
        XCTAssertEqual(LauncherChannel.configured(nil), .testedMain)
        XCTAssertEqual(LauncherChannel.configured("unrecognized"), .testedMain)
        XCTAssertEqual(LauncherChannel.alpha.title, "Alpha — latest main")
    }

    func testAlphaShowsRevisionAndDoesNotClaimCIWasRequired() throws {
        let data = Data("""
            {"ok":true,"state":"running","version":"2.7.0","build":"abcdef1234567890",
             "channel":"alpha","active_channel":"alpha","tested":false,
             "update_available":false,"latest_build":"abcdef1234567890"}
            """.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.configuredChannel, .alpha)
        XCTAssertEqual(reply.versionDescription, "WHAB 2.7.0 · abcdef1 · Alpha — latest main · CI not required")
        XCTAssertEqual(reply.updateCheckMessage, "You're up to date: revision abcdef1. CI is not required for Alpha.")
        XCTAssertTrue(reply.readyMessage.contains("without requiring CI"))
        XCTAssertTrue(reply.readyMessage.contains("local startup"))
    }

    func testChangingUpdateChannelDoesNotRelabelTheInstalledAlphaBuild() throws {
        let data = Data("""
            {"ok":true,"version":"2.7.0","build":"abcdef1234567890","channel":"main",
             "active_channel":"alpha","tested":false}
            """.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.configuredChannel, .testedMain)
        XCTAssertTrue(reply.versionDescription.contains("Alpha — latest main · CI not required"))
        XCTAssertFalse(reply.versionDescription.contains("Latest tested main"))
    }

    func testLegacyInstalledBuildDoesNotInheritConfiguredAlphaChannel() throws {
        let data = Data("""
            {"ok":true,"version":"2.6.1","build":"abcdef1234567890","channel":"alpha",
             "active_channel":null,"tested":true}
            """.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.configuredChannel, .alpha)
        XCTAssertEqual(reply.versionDescription, "WHAB 2.6.1 · abcdef1 · Installed build · CI verified")
        XCTAssertFalse(reply.versionDescription.contains("Alpha"))
        XCTAssertFalse(reply.versionDescription.contains("CI not required"))
    }

    func testLegacyUnverifiedBuildDoesNotInheritConfiguredTestedChannel() throws {
        let data = Data("""
            {"ok":true,"version":"2.6.1","build":"abcdef1234567890","channel":"main",
             "active_channel":null,"tested":false}
            """.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.configuredChannel, .testedMain)
        XCTAssertEqual(reply.versionDescription, "WHAB 2.6.1 · abcdef1 · Installed build · CI not verified")
        XCTAssertFalse(reply.versionDescription.contains("Latest tested main"))
        XCTAssertFalse(reply.versionDescription.contains("Bundled build"))
    }

    func testUpdateFeedbackUsesTargetRevisionRatherThanInstalledBuild() throws {
        let data = Data("""
            {"ok":true,"channel":"alpha","build":"aaaaaaa000000000","update_available":true,
             "latest_build":"bbbbbbb000000000","latest_version":"2.8.0"}
            """.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.updateCheckMessage, "Update available: revision bbbbbbb. Choose Update & Restart. CI is not required for Alpha.")
        XCTAssertFalse(reply.updateCheckMessage.contains("aaaaaaa"))
    }

    func testLegacyUpdateFeedbackRemainsReadableWithoutNewFields() throws {
        let data = Data("""
            {"ok":true,"channel":"main","version":"2.7.0","build":"aaaaaaa000000000",
             "update_available":true,"latest_version":"2.8.0"}
            """.utf8)
        var reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.updateCheckMessage, "Update available: version 2.8.0. Choose Update & Restart.")
        XCTAssertEqual(reply.versionDescription, "WHAB 2.7.0 · aaaaaaa · Installed build · Update available")
        reply.updateAvailable = false
        XCTAssertEqual(reply.updateCheckMessage, "You're up to date: revision aaaaaaa.")
        reply.build = nil
        reply.latestVersion = nil
        XCTAssertEqual(reply.updateCheckMessage, "You're up to date.")
    }

    func testBundledDevelopmentBuildDoesNotClaimToBeTestedMain() throws {
        let data = Data(#"{"ok":true,"channel":"main","active_channel":"seed","tested":false}"#.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertEqual(reply.versionDescription, "Bundled build · CI not verified")
    }

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

    func testAlphaConfigurationIsForwardedAsItsOwnChannel() {
        let invocation = ManagerInvocation(resources: URL(fileURLWithPath: "/Applications/WHAB.app/Contents/Resources"),
                                           root: URL(fileURLWithPath: "/private/launcher"),
                                           command: "configure", options: ["--channel", LauncherChannel.alpha.rawValue])
        XCTAssertEqual(invocation.arguments.suffix(3), ["configure", "--channel", "alpha"])
    }

    func testErrorResponseIsReadable() throws {
        let data = Data(#"{"ok":false,"error":{"code":"busy","message":"WHAB is already updating."}}"#.utf8)
        let reply = try JSONDecoder().decode(ManagerReply.self, from: data)
        XCTAssertFalse(reply.ok)
        XCTAssertEqual(reply.error?.message, "WHAB is already updating.")
    }
}

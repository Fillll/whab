import Foundation

enum LauncherChannel: String, CaseIterable {
    case testedMain = "main"
    case stable
    case alpha

    var title: String {
        switch self {
        case .testedMain: return "Latest tested main"
        case .stable: return "Stable releases"
        case .alpha: return "Alpha — latest main"
        }
    }

    static func configured(_ value: String?) -> LauncherChannel {
        LauncherChannel(rawValue: value ?? "") ?? .testedMain
    }
}

struct ManagerFailure: Decodable {
    let code: String
    let message: String
}

struct ManagerReply: Decodable {
    let ok: Bool
    var command: String?
    var state: String?
    var message: String?
    var running: Bool?
    var url: String?
    var version: String?
    var build: String?
    var channel: String?
    var activeChannel: String?
    var tested: Bool?
    var updateAvailable: Bool?
    var latestVersion: String?
    var latestBuild: String?
    var logFile: String?
    var runtimeHome: String?
    var caCertificate: String?
    var error: ManagerFailure?

    enum CodingKeys: String, CodingKey {
        case ok, command, state, message, running, url, version, build, channel, error
        case updateAvailable = "update_available", latestVersion = "latest_version"
        case activeChannel = "active_channel", latestBuild = "latest_build", tested
        case logFile = "log_file", runtimeHome = "runtime_home", caCertificate = "ca_certificate"
    }

    var displayState: String {
        switch state {
        case "running": return "Running"
        case "stopped": return "Stopped"
        case "unconfigured": return "Choose your WHAB runtime"
        default: return state?.capitalized ?? "Ready"
        }
    }

    var configuredChannel: LauncherChannel { LauncherChannel.configured(channel) }

    private var installedChannel: LauncherChannel? {
        guard let activeChannel = activeChannel else { return nil }
        return LauncherChannel(rawValue: activeChannel)
    }

    private func shortBuild(_ value: String?) -> String? {
        guard let value = value, !value.isEmpty else { return nil }
        return String(value.prefix(7))
    }

    var versionDescription: String {
        var parts = [String]()
        if let version = version { parts.append("WHAB \(version)") }
        if let revision = shortBuild(build) { parts.append(revision) }
        if let installedChannel = installedChannel {
            parts.append(installedChannel.title)
        } else if activeChannel == "seed" {
            parts.append("Bundled build")
        } else if version != nil || build != nil {
            parts.append("Installed build")
        } else {
            parts.append(configuredChannel.title)
        }
        if installedChannel == .alpha {
            parts.append("CI not required")
        } else if tested == false {
            parts.append("CI not verified")
        } else if tested == true && installedChannel == nil {
            parts.append("CI verified")
        }
        if updateAvailable == true { parts.append("Update available") }
        return parts.joined(separator: " · ")
    }

    var readyMessage: String {
        if configuredChannel == .alpha {
            return "Alpha follows the latest main revision without requiring CI. Updates still check local startup and back up your private runtime."
        }
        return "WHAB is ready. Your private runtime remains separate from installed versions."
    }

    var updateCheckMessage: String {
        let identity = shortBuild(latestBuild ?? (updateAvailable == true ? nil : build)).map { "revision \($0)" }
            ?? latestVersion.map { "version \($0)" }
        let detail = identity.map { ": \($0)" } ?? ""
        let ciNote = configuredChannel == .alpha ? " CI is not required for Alpha." : ""
        if updateAvailable == true {
            return "Update available\(detail). Choose Update & Restart.\(ciNote)"
        }
        return "You're up to date\(detail).\(ciNote)"
    }

    // Never open remote or insecure URLs supplied by a process response.
    var localURL: URL? {
        guard let raw = url, let parsed = URL(string: raw), parsed.scheme == "https",
              let host = parsed.host, ["localhost", "127.0.0.1", "::1"].contains(host),
              parsed.user == nil, parsed.password == nil else { return nil }
        return parsed
    }
}

struct LauncherMenuState {
    enum Indicator { case online, offline, working, attention }

    let title: String
    let indicator: Indicator
    var opensSettings = false
    var isEnabled = true

    init(reply: ManagerReply?, activity: String? = nil, needsAttention: Bool = false) {
        if let activity = activity {
            title = activity
            indicator = .working
            isEnabled = false
        } else if needsAttention {
            title = "Needs attention — Settings…"
            indicator = .attention
            opensSettings = true
        } else if let reply = reply {
            if reply.state == "unconfigured" {
                title = "Setup needed — Settings…"
                indicator = .offline
                opensSettings = true
            } else if reply.running == true && reply.localURL != nil {
                title = "Online — Open"
                indicator = .online
            } else if reply.state == "stopped" && reply.running == false {
                title = "Offline — Start"
                indicator = .offline
            } else {
                title = "Unavailable — Settings…"
                indicator = .attention
                opensSettings = true
            }
        } else {
            title = "Checking…"
            indicator = .working
            isEnabled = false
        }
    }
}

struct ManagerInvocation {
    let executable: URL
    let arguments: [String]

    init(resources: URL, root: URL, command: String, options: [String] = []) {
        executable = resources.appendingPathComponent("python/bin/python3.14")
        arguments = [
            "-I", "-B",
            resources.appendingPathComponent("manager/launcher.py").path,
            "--root", root.path,
            "--python", executable.path,
            "--seed", resources.appendingPathComponent("seed").path,
            "--wheelhouse", resources.appendingPathComponent("wheelhouse").path,
            command,
        ] + options
    }
}

enum ManagerRunner {
    static func run(_ invocation: ManagerInvocation) -> (Data, Int32) {
        let process = Process()
        process.executableURL = invocation.executable
        process.arguments = invocation.arguments
        var environment = ProcessInfo.processInfo.environment
        // Python must not import code or inherit virtualenv paths from another installation.
        for key in ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"] {
            environment.removeValue(forKey: key)
        }
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        process.standardInput = FileHandle.nullDevice
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (data, process.terminationStatus)
        } catch {
            let envelope: [String: Any] = [
                "ok": false,
                "error": ["code": "launcher_runtime_missing", "message":
                    "The bundled Python runtime could not start. Rebuild or reinstall WHAB.app."],
            ]
            return ((try? JSONSerialization.data(withJSONObject: envelope)) ?? Data(), 1)
        }
    }
}

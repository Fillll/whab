import Foundation

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
    var updateAvailable: Bool?
    var latestVersion: String?
    var logFile: String?
    var runtimeHome: String?
    var caCertificate: String?
    var error: ManagerFailure?

    enum CodingKeys: String, CodingKey {
        case ok, command, state, message, running, url, version, build, channel, error
        case updateAvailable = "update_available", latestVersion = "latest_version"
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

    // Never open remote or insecure URLs supplied by a process response.
    var localURL: URL? {
        guard let raw = url, let parsed = URL(string: raw), parsed.scheme == "https",
              let host = parsed.host, ["localhost", "127.0.0.1", "::1"].contains(host),
              parsed.user == nil, parsed.password == nil else { return nil }
        return parsed
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

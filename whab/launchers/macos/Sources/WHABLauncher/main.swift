import AppKit
import Foundation
import ServiceManagement

// An explicit alternative launcher root supports isolated demos and diagnostics.
// It never changes the default user's runtime unless supplied on the command line.
func launcherRoot() -> URL {
    if let index = CommandLine.arguments.firstIndex(of: "--root"),
       CommandLine.arguments.indices.contains(index + 1) {
        let path = CommandLine.arguments[index + 1]
        guard path.hasPrefix("/") else {
            FileHandle.standardError.write(Data("--root requires an absolute path.\n".utf8))
            exit(2)
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }
    return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("WHAB/launcher", isDirectory: true)
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem!
    private var window: NSWindow!
    private var reply: ManagerReply?
    private var busy = false
    private var menuActivity: String?
    private var needsAttention = false
    private var timer: Timer?
    private let worker = DispatchQueue(label: "com.whab.launcher.manager", qos: .userInitiated)
    private let statusLabel = NSTextField(labelWithString: "Checking WHAB…")
    private let versionLabel = NSTextField(wrappingLabelWithString: "")
    private let messageLabel = NSTextField(wrappingLabelWithString: "")
    private let runtimeField = NSTextField(string: "")
    private let channel = NSPopUpButton()
    private let spinner = NSProgressIndicator()
    private let login = NSButton(checkboxWithTitle: "Open launcher at login", target: nil, action: nil)
    private var actionButtons: [NSButton] = []
    private var startButton: NSButton!
    private let root = launcherRoot()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let applicationMenu = NSMenu()
        let applicationItem = NSMenuItem(title: "WHAB", action: nil, keyEquivalent: "")
        let applicationSubmenu = NSMenu(title: "WHAB")
        applicationSubmenu.addItem(withTitle: "WHAB settings…", action: #selector(showWindow), keyEquivalent: ",").target = self
        applicationSubmenu.addItem(.separator())
        applicationSubmenu.addItem(withTitle: "Quit launcher", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q").target = NSApp
        applicationItem.submenu = applicationSubmenu
        applicationMenu.addItem(applicationItem)
        let windowItem = NSMenuItem(title: "Window", action: nil, keyEquivalent: "")
        let windowSubmenu = NSMenu(title: "Window")
        windowSubmenu.addItem(withTitle: "Close window", action: #selector(closeWindow), keyEquivalent: "w").target = self
        windowItem.submenu = windowSubmenu
        applicationMenu.addItem(windowItem)
        NSApp.mainMenu = applicationMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "WHAB"
        statusItem.button?.image = NSImage(systemSymbolName: "chart.pie.fill", accessibilityDescription: "WHAB")
        statusItem.button?.image?.isTemplate = true
        statusItem.button?.imagePosition = .imageLeading
        statusItem.button?.toolTip = "WHAB — starting launcher"
        buildWindow()
        refreshMenu()
        showWindow()
        perform("status", silent: true,
                launchOnReady: !CommandLine.arguments.contains("--no-auto-start"))
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            guard let self = self, !self.busy else { return }
            self.perform("status", silent: true)
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if CommandLine.arguments.contains("--no-auto-start") || busy || reply?.state == "unconfigured" {
            showWindow()
        } else {
            startOrOpen()
        }
        return true
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 570, height: 570),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered,
                          defer: false)
        window.title = "WHAB"
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        let content = NSStackView()
        content.orientation = .vertical
        content.alignment = .leading
        content.spacing = 15
        content.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(content)
        if let view = window.contentView {
            NSLayoutConstraint.activate([
                content.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 28),
                content.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -28),
                content.topAnchor.constraint(equalTo: view.topAnchor, constant: 26),
                content.bottomAnchor.constraint(lessThanOrEqualTo: view.bottomAnchor, constant: -24),
            ])
        }
        let title = NSTextField(labelWithString: "Your budget, ready when you are.")
        title.font = .systemFont(ofSize: 24, weight: .semibold)
        content.addArrangedSubview(title)
        let statusRow = NSStackView()
        statusRow.spacing = 9
        statusLabel.font = .systemFont(ofSize: 15, weight: .medium)
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        statusRow.addArrangedSubview(spinner)
        statusRow.addArrangedSubview(statusLabel)
        content.addArrangedSubview(statusRow)
        versionLabel.textColor = .secondaryLabelColor
        versionLabel.font = .systemFont(ofSize: 12)
        versionLabel.maximumNumberOfLines = 2
        content.addArrangedSubview(versionLabel)
        versionLabel.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true

        let primary = NSStackView()
        primary.spacing = 8
        startButton = button("Start WHAB", #selector(startOrOpen))
        startButton.keyEquivalent = "\r"
        primary.addArrangedSubview(startButton)
        primary.addArrangedSubview(button("Restart", #selector(restart)))
        primary.addArrangedSubview(button("Stop", #selector(stop)))
        content.addArrangedSubview(primary)
        let updates = NSStackView()
        updates.spacing = 8
        updates.addArrangedSubview(button("Update & Restart", #selector(update)))
        updates.addArrangedSubview(button("Check for updates", #selector(checkUpdates)))
        content.addArrangedSubview(updates)
        messageLabel.font = .systemFont(ofSize: 12)
        messageLabel.textColor = .secondaryLabelColor
        messageLabel.maximumNumberOfLines = 5
        messageLabel.stringValue = "Start checks for the latest tested version. Closing this window keeps WHAB available in the menu bar."
        content.addArrangedSubview(messageLabel)
        messageLabel.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true

        let separator = NSBox()
        separator.boxType = .separator
        content.addArrangedSubview(separator)
        separator.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true
        let runtimeTitle = NSTextField(labelWithString: "Private runtime folder")
        runtimeTitle.font = .systemFont(ofSize: 13, weight: .semibold)
        content.addArrangedSubview(runtimeTitle)
        let runtimeRow = NSStackView()
        runtimeRow.spacing = 8
        runtimeField.isEditable = false
        runtimeField.isSelectable = true
        runtimeField.placeholderString = "New users: use the default folder. Existing users: choose your runtime."
        runtimeField.lineBreakMode = .byTruncatingMiddle
        runtimeField.setContentHuggingPriority(.defaultLow, for: .horizontal)
        runtimeRow.addArrangedSubview(runtimeField)
        runtimeRow.addArrangedSubview(button("Choose…", #selector(chooseRuntime)))
        content.addArrangedSubview(runtimeRow)
        runtimeRow.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true

        let settings = NSStackView()
        settings.spacing = 10
        settings.addArrangedSubview(NSTextField(labelWithString: "Updates"))
        for option in LauncherChannel.allCases {
            channel.addItem(withTitle: option.title)
            channel.lastItem?.representedObject = option.rawValue
        }
        channel.target = self
        channel.action = #selector(changeChannel)
        settings.addArrangedSubview(channel)
        settings.addArrangedSubview(button("Use default runtime", #selector(defaultRuntime)))
        content.addArrangedSubview(settings)
        login.target = self
        login.action = #selector(changeLogin)
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        content.addArrangedSubview(login)
        let support = NSStackView()
        support.spacing = 8
        support.addArrangedSubview(button("View logs", #selector(openLogs)))
        support.addArrangedSubview(button("HTTPS certificate…", #selector(openCertificate)))
        content.addArrangedSubview(support)
        let footer = NSTextField(labelWithString: "WHAB Launcher \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "development") · macOS")
        footer.font = .systemFont(ofSize: 11)
        footer.textColor = .tertiaryLabelColor
        content.addArrangedSubview(footer)
    }

    private func button(_ title: String, _ action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .rounded
        actionButtons.append(button)
        return button
    }

    @objc private func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func closeWindow() { window.performClose(nil) }

    private var menuState: LauncherMenuState {
        LauncherMenuState(reply: reply, activity: menuActivity, needsAttention: needsAttention)
    }

    private func refreshMenu() {
        let menu = NSMenu()
        let state = menuState
        let primary = menu.addItem(withTitle: state.title, action: #selector(openFromMenu), keyEquivalent: "")
        primary.target = self
        primary.isEnabled = !busy && state.isEnabled
        let color: NSColor
        switch state.indicator {
        case .online: color = .systemGreen
        case .offline: color = .secondaryLabelColor
        case .working: color = .systemOrange
        case .attention: color = .systemRed
        }
        let dot = NSImage(size: NSSize(width: 12, height: 12), flipped: false) { _ in
            color.setFill()
            NSBezierPath(ovalIn: NSRect(x: 3, y: 3, width: 6, height: 6)).fill()
            return true
        }
        dot.isTemplate = false
        primary.image = dot
        menu.addItem(withTitle: "WHAB settings…", action: #selector(showWindow), keyEquivalent: ",").target = self
        menu.addItem(withTitle: "Quit launcher", action: #selector(quit), keyEquivalent: "q").target = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        statusItem.button?.toolTip = "WHAB — \(state.title)"
    }

    @objc private func openFromMenu() {
        if menuState.opensSettings { showWindow() }
        else { startOrOpen() }
    }

    private func perform(_ command: String, options: [String] = [], silent: Bool = false,
                         openAfter: Bool = false, launchOnReady: Bool = false) {
        guard !busy else { return }
        busy = true
        if !silent {
            statusLabel.stringValue = ["start": "Starting WHAB…", "restart": "Restarting WHAB…",
                "stop": "Stopping WHAB…", "update": "Preparing update…",
                "check-update": "Checking for updates…", "configure": "Saving settings…"][command] ?? "Working…"
            menuActivity = statusLabel.stringValue
            messageLabel.stringValue = "WHAB may need a moment to prepare an installation or make a private backup."
            spinner.startAnimation(nil)
        }
        actionButtons.forEach { $0.isEnabled = false }
        channel.isEnabled = false
        refreshMenu()
        let invocation = ManagerInvocation(resources: Bundle.main.resourceURL!, root: root,
                                           command: command, options: options)
        worker.async { [weak self] in
            let (data, _) = ManagerRunner.run(invocation)
            let result = try? JSONDecoder().decode(ManagerReply.self, from: data)
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.busy = false
                self.menuActivity = nil
                self.spinner.stopAnimation(nil)
                self.actionButtons.forEach { $0.isEnabled = true }
                self.channel.isEnabled = true
                if let result = result, result.ok {
                    self.needsAttention = false
                    self.reply = result
                    self.statusLabel.stringValue = result.displayState
                    self.versionLabel.stringValue = result.versionDescription
                    self.runtimeField.stringValue = result.runtimeHome ?? self.runtimeField.stringValue
                    self.channel.selectItem(withTitle: result.configuredChannel.title)
                    self.startButton.title = result.running == true ? "Open WHAB" : "Start WHAB"
                    if result.state == "unconfigured" {
                        self.messageLabel.stringValue = "Choose your existing WHAB runtime folder, or select Use default runtime to start with a new private budget environment."
                    }
                    if !silent {
                        self.messageLabel.stringValue = result.message ?? (command == "check-update"
                            ? result.updateCheckMessage
                            : (result.updateAvailable == true
                                ? "An update is available. Choose Update & Restart when you are ready."
                                : result.readyMessage))
                    }
                    if openAfter, let url = result.localURL { NSWorkspace.shared.open(url) }
                    self.refreshMenu()
                    if launchOnReady && result.state != "unconfigured" {
                        self.startOrOpen()
                    } else if command != "status" {
                        self.perform("status", silent: true)
                    }
                } else {
                    self.needsAttention = true
                    self.statusLabel.stringValue = "Needs attention"
                    self.messageLabel.stringValue = result?.error?.message ?? "The launcher could not read WHAB's response. Open logs for details."
                    if let previous = self.reply {
                        self.channel.selectItem(withTitle: previous.configuredChannel.title)
                    }
                    self.refreshMenu()
                    if !silent { self.showWindow() }
                }
            }
        }
    }

    @objc private func startOrOpen() {
        if reply?.running == true, let url = reply?.localURL { NSWorkspace.shared.open(url) }
        else { perform("start", openAfter: true) }
    }
    @objc private func restart() { perform("restart", openAfter: true) }
    @objc private func update() { perform("update", openAfter: true) }
    @objc private func stop() { perform("stop") }
    @objc private func checkUpdates() { perform("check-update") }

    @objc private func chooseRuntime() {
        let panel = NSOpenPanel()
        panel.title = "Choose your WHAB runtime folder"
        panel.message = "Choose the folder containing your WHAB .env, data, budgets and tls. Keep this folder and its encryption keys together. Stop any manually started WHAB server before adopting it."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            perform("configure", options: ["--runtime-home", url.path, "--channel", selectedChannel])
        }
    }
    private var selectedChannel: String {
        LauncherChannel.configured(channel.selectedItem?.representedObject as? String).rawValue
    }
    @objc private func changeChannel() { perform("configure", options: ["--channel", selectedChannel]) }
    @objc private func defaultRuntime() {
        let runtime = root.deletingLastPathComponent().appendingPathComponent("runtime")
        perform("configure", options: ["--runtime-home", runtime.path, "--channel", selectedChannel])
    }

    @objc private func changeLogin() {
        do {
            if login.state == .on { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            if SMAppService.mainApp.status == .requiresApproval {
                messageLabel.stringValue = "Allow WHAB in System Settings → General → Login Items to finish enabling startup."
                SMAppService.openSystemSettingsLoginItems()
            }
        } catch {
            login.state = SMAppService.mainApp.status == .enabled ? .on : .off
            messageLabel.stringValue = "macOS could not change login startup. Install WHAB.app in Applications and check System Settings → General → Login Items."
        }
    }

    @objc private func openLogs() {
        let candidates: [String?] = [reply?.logFile,
                          root.appendingPathComponent("logs/install.log").path,
                          root.appendingPathComponent("logs/smoke.log").path,
                          root.appendingPathComponent("logs/manager.log").path]
        let existing = candidates.compactMap { $0 }.filter { FileManager.default.fileExists(atPath: $0) }
        let newest = existing.max { left, right in
            let leftDate = (try? FileManager.default.attributesOfItem(atPath: left)[.modificationDate]) as? Date ?? .distantPast
            let rightDate = (try? FileManager.default.attributesOfItem(atPath: right)[.modificationDate]) as? Date ?? .distantPast
            return leftDate < rightDate
        }
        guard let path = newest else {
            messageLabel.stringValue = "Logs appear after WHAB begins preparing or starting an installation."
            showWindow()
            return
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    @objc private func openCertificate() {
        let path = reply?.caCertificate ?? reply?.runtimeHome.map { "\($0)/tls/ca.crt" }
        guard let path = path, FileManager.default.fileExists(atPath: path) else {
            messageLabel.stringValue = "Start WHAB first to create its local HTTPS certificate."
            showWindow()
            return
        }
        let alert = NSAlert()
        alert.messageText = "Open your WHAB certificate in Keychain Access?"
        alert.informativeText = "Your browser needs to trust this runtime's local CA. In Keychain Access, add the certificate, open it, expand Trust, and choose Always Trust for SSL. macOS may request your password. WHAB does not change trust automatically."
        alert.addButton(withTitle: "Open certificate")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
        }
    }
    @objc private func quit() { NSApp.terminate(nil) }
}

if CommandLine.arguments.contains("--diagnose") {
    let invocation = ManagerInvocation(resources: Bundle.main.resourceURL!, root: launcherRoot(), command: "doctor")
    let (data, code) = ManagerRunner.run(invocation)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(code)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()

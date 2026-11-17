import AppKit
import Darwin
import Foundation

private enum LauncherLogReadError: Error { case unavailable, notRegular }

enum LauncherLog: String, CaseIterable {
    case whab, install, smoke, manager

    var title: String {
        switch self {
        case .whab: return "WHAB"
        case .install: return "Installation"
        case .smoke: return "Sandbox startup"
        case .manager: return "Launcher manager"
        }
    }

    func fileURL(root: URL) -> URL {
        root.appendingPathComponent("logs", isDirectory: true).appendingPathComponent(rawValue + ".log")
    }

    func read(root: URL, limit: Int = 262_144) -> String {
        do {
            // Anchor both child opens to already opened directories. Path validation
            // followed by a path-based read would race a replaced log or directory.
            let directoryFlags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
            let rootDescriptor = Darwin.open(root.path, directoryFlags)
            guard rootDescriptor >= 0 else { throw LauncherLogReadError.unavailable }
            defer { Darwin.close(rootDescriptor) }
            let directoryDescriptor = openat(rootDescriptor, "logs", directoryFlags)
            guard directoryDescriptor >= 0 else {
                throw errno == ELOOP || errno == ENOTDIR ? LauncherLogReadError.notRegular : .unavailable
            }
            defer { Darwin.close(directoryDescriptor) }
            // A FIFO/device swapped into the path must neither block nor be read.
            let descriptor = openat(directoryDescriptor, rawValue + ".log",
                                    O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
            guard descriptor >= 0 else {
                throw errno == ELOOP ? LauncherLogReadError.notRegular : .unavailable
            }
            let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            defer { try? file.close() }
            var info = stat()
            guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG else {
                throw LauncherLogReadError.notRegular
            }
            let size = try file.seekToEnd()
            let count = UInt64(max(1, limit))
            let offset = size > count ? size - count : 0
            try file.seek(toOffset: offset)
            let bytes = try file.read(upToCount: Int(count)) ?? Data()
            if bytes.isEmpty { return "This log is empty. Choose another log or Refresh after an action." }
            var content = String(decoding: bytes, as: UTF8.self)
            if offset > 0 {
                // The bounded read may begin in a UTF-8 character or partial line.
                if let newline = content.firstIndex(of: "\n") { content.removeSubrange(...newline) }
                content = "[Showing the latest \(count) bytes. Reveal in Finder to open the complete file.]\n\n" + content
            }
            return content
        } catch LauncherLogReadError.notRegular {
            return "This log is not a regular local file. No contents were opened."
        } catch {
            return "This log is not available yet. Logs appear after WHAB prepares or starts an installation. Choose another log or Refresh."
        }
    }
}

final class LauncherLogWindowController: NSWindowController {
    private let root: URL
    private let chooser = NSPopUpButton()
    private let text = NSTextView()
    private let location = NSTextField(wrappingLabelWithString: "")

    init(root: URL) {
        self.root = root.resolvingSymlinksInPath()
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 560),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        super.init(window: window)
        window.title = "WHAB private logs"
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 520, height: 340)
        window.center()
        let content = NSStackView()
        content.orientation = .vertical
        content.alignment = .leading
        content.spacing = 12
        content.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(content)
        let toolbar = NSStackView()
        toolbar.orientation = .horizontal
        toolbar.spacing = 10
        chooser.addItems(withTitles: LauncherLog.allCases.map(\.title))
        chooser.target = self
        chooser.action = #selector(refresh)
        chooser.setAccessibilityLabel("Log file")
        toolbar.addArrangedSubview(chooser)
        toolbar.addArrangedSubview(NSButton(title: "Refresh", target: self, action: #selector(refresh)))
        toolbar.addArrangedSubview(NSButton(title: "Reveal in Finder", target: self, action: #selector(reveal)))
        content.addArrangedSubview(toolbar)
        content.addArrangedSubview(NSTextField(wrappingLabelWithString: "Local, read-only logs. Nothing is uploaded. Logs may contain private details; review before sharing."))
        location.font = .systemFont(ofSize: 11)
        location.textColor = .secondaryLabelColor
        location.isSelectable = true
        content.addArrangedSubview(location)
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.borderType = .bezelBorder
        text.isEditable = false
        text.isSelectable = true
        text.isRichText = false
        text.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        text.isHorizontallyResizable = true
        text.isVerticallyResizable = true
        text.autoresizingMask = [.width]
        text.textContainer?.widthTracksTextView = false
        text.textContainer?.containerSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                                                  height: CGFloat.greatestFiniteMagnitude)
        text.setAccessibilityLabel("Private log contents")
        scroll.documentView = text
        content.addArrangedSubview(scroll)
        if let view = window.contentView {
            NSLayoutConstraint.activate([
                content.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 18),
                content.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -18),
                content.topAnchor.constraint(equalTo: view.topAnchor, constant: 18),
                content.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -18),
                scroll.widthAnchor.constraint(equalTo: content.widthAnchor),
                scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 160),
            ])
        }
    }

    required init?(coder: NSCoder) { nil }

    private var selectedLog: LauncherLog { LauncherLog.allCases[max(0, chooser.indexOfSelectedItem)] }

    func open() {
        refresh()
        showWindow(nil)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func refresh() {
        location.stringValue = selectedLog.fileURL(root: root).path
        text.string = selectedLog.read(root: root)
        text.scrollToEndOfDocument(nil)
    }

    @objc private func reveal() {
        let url = selectedLog.fileURL(root: root)
        if FileManager.default.fileExists(atPath: url.path) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } else {
            refresh()
        }
    }
}

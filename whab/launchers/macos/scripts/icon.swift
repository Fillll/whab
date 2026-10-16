import AppKit
import Foundation

// An original vector mark rendered locally; no remote assets or font downloads.
let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let representation = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels,
                                               pixelsHigh: pixels, bitsPerSample: 8,
                                               samplesPerPixel: 4, hasAlpha: true,
                                               isPlanar: false, colorSpaceName: .deviceRGB,
                                               bytesPerRow: 0, bitsPerPixel: 0)!
        let context = NSGraphicsContext(bitmapImageRep: representation)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        let factor = CGFloat(pixels) / 1024
        context.cgContext.scaleBy(x: factor, y: factor)
        NSColor(calibratedRed: 0.10, green: 0.34, blue: 0.28, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 62, y: 62, width: 900, height: 900),
                     xRadius: 208, yRadius: 208).fill()
        let mark = NSBezierPath()
        mark.move(to: NSPoint(x: 252, y: 674))
        mark.line(to: NSPoint(x: 356, y: 352))
        mark.line(to: NSPoint(x: 512, y: 594))
        mark.line(to: NSPoint(x: 668, y: 352))
        mark.line(to: NSPoint(x: 772, y: 674))
        mark.lineWidth = 75
        mark.lineCapStyle = .round
        mark.lineJoinStyle = .round
        NSColor(calibratedWhite: 0.97, alpha: 1).setStroke()
        mark.stroke()
        NSColor(calibratedRed: 0.93, green: 0.72, blue: 0.36, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 469, y: 724, width: 86, height: 86)).fill()
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let file = directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png")
        try representation.representation(using: .png, properties: [:])!.write(to: file)
    }
}

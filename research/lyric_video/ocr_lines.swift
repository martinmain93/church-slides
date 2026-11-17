import AppKit
import Foundation
import Vision

/// Read image paths on stdin, write one JSON object per line.
/// Research helper for lyric-video goldens. Not part of the app.
struct Hit: Encodable {
    let text: String
    let confidence: Float
    /// Vision's origin is the bottom of the frame. Higher is higher on screen.
    let y: Float
}

struct Row: Encodable {
    let path: String
    let lines: [Hit]
    let error: String?
}

let encoder = JSONEncoder()

func emit(_ row: Row) {
    let data = try! encoder.encode(row)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

while let line = readLine() {
    let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
    if path.isEmpty { continue }
    guard let image = NSImage(contentsOfFile: path),
          let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let cg = rep.cgImage
    else {
        emit(Row(path: path, lines: [], error: "unreadable"))
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do {
        try handler.perform([request])
        let hits = (request.results ?? []).compactMap { obs -> Hit? in
            guard let best = obs.topCandidates(1).first else { return nil }
            return Hit(text: best.string, confidence: best.confidence, y: Float(obs.boundingBox.origin.y))
        }
        emit(Row(path: path, lines: hits, error: nil))
    } catch {
        emit(Row(path: path, lines: [], error: String(describing: error)))
    }
}

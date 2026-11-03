import Foundation

enum LittleEndianPCMConverter {
    static func convertFloat32ToInt16(_ samples: UnsafeBufferPointer<Float>) -> Data {
        var output = Data(capacity: samples.count * 2)
        for sample in samples {
            let clipped = max(-1.0, min(1.0, sample))
            let value = Int16(clipped * 32767.0)
            let bits = UInt16(bitPattern: value)
            output.append(UInt8(truncatingIfNeeded: bits))
            output.append(UInt8(truncatingIfNeeded: bits >> 8))
        }
        return output
    }
}

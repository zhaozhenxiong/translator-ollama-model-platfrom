import Foundation

struct PCMConverterSelfTestReport: Encodable {
    let clipped_little_endian_samples: String
    let empty_input: String
}

private struct PCMConverterSelfTestFailure: Error, CustomStringConvertible {
    let description: String
}

func runPCMConverterSelfTests() throws -> PCMConverterSelfTestReport {
    let samples: [Float] = [0, 0.5, -0.5, 1, -1, 2, -2]
    let data = samples.withUnsafeBufferPointer {
        LittleEndianPCMConverter.convertFloat32ToInt16($0)
    }
    guard data.count == samples.count * 2 else {
        throw PCMConverterSelfTestFailure(description: "PCM output has the wrong byte count")
    }
    let values = stride(from: 0, to: data.count, by: 2).map { offset in
        Int16(bitPattern: UInt16(data[offset]) | UInt16(data[offset + 1]) << 8)
    }
    let expected: [Int16] = [0, 16383, -16383, 32767, -32767, 32767, -32767]
    guard values == expected else {
        throw PCMConverterSelfTestFailure(description: "Unexpected PCM values: \(values)")
    }

    let empty: [Float] = []
    let emptyData = empty.withUnsafeBufferPointer {
        LittleEndianPCMConverter.convertFloat32ToInt16($0)
    }
    guard emptyData.isEmpty else {
        throw PCMConverterSelfTestFailure(description: "Empty input produced PCM bytes")
    }
    return PCMConverterSelfTestReport(
        clipped_little_endian_samples: "passed",
        empty_input: "passed"
    )
}

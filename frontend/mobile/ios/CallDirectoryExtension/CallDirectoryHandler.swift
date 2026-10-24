import CallKit
import Foundation

final class CallDirectoryHandler: CXCallDirectoryProvider {
    private let appGroup = "group.com.voiceshield.calls"
    private let spamNumbersKey = "spamNumbers"

    override func beginRequest(with context: CXCallDirectoryExtensionContext) {
        guard let defaults = UserDefaults(suiteName: appGroup) else {
            context.cancelRequest(withError: CallDirectoryError.sharedContainerUnavailable)
            return
        }

        let values = defaults.array(forKey: spamNumbersKey) as? [String] ?? []
        let numbers = Set(values.compactMap(Self.phoneNumberValue)).sorted()
        for number in numbers {
            context.addIdentificationEntry(
                withNextSequentialPhoneNumber: number,
                label: "VoiceShield user-reported spam",
            )
        }
        context.completeRequest()
    }

    private static func phoneNumberValue(_ value: String) -> Int64? {
        let digits = value.filter { $0.isNumber }
        guard (3...15).contains(digits.count) else { return nil }
        return Int64(digits)
    }
}

private enum CallDirectoryError: Error {
    case sharedContainerUnavailable
}

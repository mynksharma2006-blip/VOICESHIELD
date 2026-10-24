import CallKit
import SwiftUI

struct SpamNumberListView: View {
    private let appGroup = "group.com.voiceshield.calls"
    private let spamNumbersKey = "spamNumbers"
    private let extensionIdentifier = "com.voiceshield.calls.CallDirectoryExtension"

    @State private var number = ""
    @State private var spamNumbers: [String]
    @State private var status = "Numbers stay on this device. Only entries you add are identified."

    init() {
        let saved = UserDefaults(suiteName: "group.com.voiceshield.calls")?
            .stringArray(forKey: "spamNumbers") ?? []
        _spamNumbers = State(initialValue: saved.sorted())
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Add a reported number") {
                    TextField("Phone number with country code", text: $number)
                        .keyboardType(.phonePad)
                    Button("Add to local spam list", action: addNumber)
                }
                Section("Numbers identified by VoiceShield") {
                    if spamNumbers.isEmpty {
                        Text("No numbers added")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(spamNumbers, id: \.self) { entry in
                        HStack {
                            Text(entry)
                            Spacer()
                            Button("Remove", role: .destructive) {
                                removeNumber(entry)
                            }
                        }
                    }
                }
                Section {
                    Text(status)
                    Text("iOS identifies only numbers in this list. This does not detect unknown callers or analyze call audio. Enable VoiceShield in Settings > Phone > Call Blocking & Identification.")
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("VoiceShield Calls")
        }
    }

    private func addNumber() {
        let normalized = number.filter { $0.isNumber }
        guard (3...15).contains(normalized.count) else {
            status = "Enter a phone number with country code, containing 3 to 15 digits."
            return
        }
        guard !spamNumbers.contains(normalized) else {
            status = "That number is already on your local list."
            return
        }
        spamNumbers.append(normalized)
        spamNumbers.sort()
        number = ""
        saveAndReload()
    }

    private func removeNumber(_ value: String) {
        spamNumbers.removeAll { $0 == value }
        saveAndReload()
    }

    private func saveAndReload() {
        guard let defaults = UserDefaults(suiteName: appGroup) else {
            status = "Could not access the shared app-group storage."
            return
        }
        defaults.set(spamNumbers, forKey: spamNumbersKey)
        CXCallDirectoryManager.sharedInstance.reloadExtension(
            withIdentifier: extensionIdentifier,
        ) { error in
            DispatchQueue.main.async {
                if let error {
                    status = "Saved locally, but iOS could not refresh call identification: \(error.localizedDescription)"
                } else {
                    status = "Saved. iOS call identification has been refreshed."
                }
            }
        }
    }
}

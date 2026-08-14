import SwiftUI

public struct SovereignNodeView: View {
    private let client: SovereignNodeClient
    @State private var pairingCode = ""
    @State private var path = "hello.txt"
    @State private var query = "Reply with exactly LOOPBACK_OK."
    @State private var approvalID = ""
    @State private var commandSHA256 = ""
    @State private var writeApprovalID = ""
    @State private var writeTargetPath = ""
    @State private var writePayloadSHA256 = ""
    @State private var writeExpectedSHA256 = ""
    @State private var policyVersion = "vesta-policy-1"
    @State private var output = "No receipt yet"
    @State private var busy = false
    @State private var authenticated = false
    @State private var vestaStatus: VestaStatus?
    @State private var nodeStatus: NodeStatusResponse?

    public init(client: SovereignNodeClient) {
        self.client = client
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section("Node") {
                    LabeledContent("Device", value: client.deviceID)
                    LabeledContent("Key assurance", value: client.keyAssurance.rawValue)
                    LabeledContent("Vesta", value: vestaStatus?.status ?? "unknown")
                    LabeledContent("Node", value: nodeStatus?.status ?? "unknown")
                    LabeledContent("Transport", value: transportLabel)

                    HStack {
                        Button("Refresh status") {
                            Task { await refreshStatus() }
                        }
                        Spacer()
                        if busy {
                            ProgressView()
                        }
                    }
                }

                Section("Governance") {
                    LabeledContent("Mutation policy", value: "owner approval")
                    LabeledContent("Kill state", value: killStateLabel)
                    Text("This device surface cannot self-approve mutations. Approval remains an operator action, and kill/quarantine is fail-closed.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Device session") {
                    SecureField("Pairing code", text: $pairingCode)
                    Button(authenticated ? "Session authenticated" : "Enroll and authenticate") {
                        Task { await authenticate() }
                    }
                    .disabled(busy || pairingCode.isEmpty || authenticated)

                    Text("Loopback development uses the same signed node.v1 protocol as the iPhone path. It does not grant hardware assurance.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Signed chat") {
                    TextEditor(text: $query)
                        .frame(minHeight: 80)
                        .autocorrectionDisabled()
                    Button("Send signed request") {
                        Task { await sendChat() }
                    }
                    .disabled(busy || !authenticated || query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                Section("Vesta read-only filesystem") {
                    TextField("Sandbox file", text: $path)
                        .autocorrectionDisabled()
                    Button("Read file") {
                        Task { await readFile() }
                    }
                    .disabled(busy || !authenticated || path.isEmpty)
                    Text("Writes, shell, sensors, GUI, and external messaging remain behind Vesta policy and owner approval.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Signed owner approval") {
                    TextField("Approval ID", text: $approvalID)
                        .autocorrectionDisabled()
                    TextField("Exact command SHA-256", text: $commandSHA256)
                        .autocorrectionDisabled()
                    TextField("Policy version", text: $policyVersion)
                        .autocorrectionDisabled()
                    Button("Sign and approve exact shell contract") {
                        Task { await approveShell() }
                    }
                    .disabled(
                        busy || !authenticated || approvalID.isEmpty ||
                        commandSHA256.count != 64 || policyVersion.isEmpty
                    )
                    Text("The signature covers the approval ID, command hash, and policy version. Vesta rejects any mismatch, replay, expiry, or already-decided approval.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Signed file-write approval") {
                    TextField("Write approval ID", text: $writeApprovalID)
                        .autocorrectionDisabled()
                    TextField("Exact target path", text: $writeTargetPath)
                        .autocorrectionDisabled()
                    TextField("Payload SHA-256", text: $writePayloadSHA256)
                        .autocorrectionDisabled()
                    TextField("Precondition SHA-256 or blank", text: $writeExpectedSHA256)
                        .autocorrectionDisabled()
                    Button("Sign and approve exact file write") {
                        Task { await approveFileWrite() }
                    }
                    .disabled(
                        busy || !authenticated || writeApprovalID.isEmpty || writeTargetPath.isEmpty ||
                        writePayloadSHA256.count != 64 ||
                        (!writeExpectedSHA256.isEmpty && writeExpectedSHA256.count != 64) ||
                        policyVersion.isEmpty
                    )
                    Text("The signed ACK covers the path, payload hash, precondition hash, and policy version. It cannot be reused for another write.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Receipt") {
                    ScrollView {
                        Text(output)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .font(.system(.footnote, design: .monospaced))
                            .textSelection(.enabled)
                    }
                    .frame(minHeight: 120)
                }
            }
            .navigationTitle("Sovereign Node")
            .task { await refreshStatus() }
        }
    }

    private var killStateLabel: String {
        guard let nodeStatus else { return "unknown" }
        return nodeStatus.status == "QUARANTINED" ? "quarantined" : "not armed"
    }

    private var transportLabel: String {
        guard let vestaStatus else { return "unknown" }
        return vestaStatus.transportRequired ? "private tunnel required" : "local development"
    }

    @MainActor
    private func refreshStatus() async {
        busy = true
        defer { busy = false }
        do {
            async let vesta = client.vestaStatus()
            async let node = client.nodeStatus()
            vestaStatus = try await vesta
            nodeStatus = try await node
            output = "Vesta \(vestaStatus?.status ?? "unknown") · Node \(nodeStatus?.status ?? "unknown")"
        } catch {
            output = "Status failed: \(error)"
        }
    }

    @MainActor
    private func authenticate() async {
        busy = true
        defer { busy = false }
        do {
            try await client.enroll(pairingCode: pairingCode, hardwareAssurance: client.keyAssurance.rawValue)
            try await client.authenticate()
            authenticated = true
            output = "Authenticated signed session established"
        } catch {
            output = "Authentication failed: \(error)"
        }
    }

    @MainActor
    private func sendChat() async {
        busy = true
        defer { busy = false }
        do {
            let response = try await client.chat(query)
            let task = try await client.task(response.taskID)
            output = "decision=\(response.decision)\nstate=\(task.state)\ntask=\(response.taskID)\nbind=\(response.bindID)\nevidence=\(response.evidenceRefs.joined(separator: ", "))\n\(response.payload["text"] ?? response.error ?? "no response")"
        } catch {
            output = "Signed request failed: \(error)"
        }
    }

    @MainActor
    private func approveFileWrite() async {
        busy = true
        defer { busy = false }
        do {
            let response = try await client.approveFileWrite(
                writeApprovalID,
                targetPath: writeTargetPath,
                payloadSHA256: writePayloadSHA256,
                expectedSHA256: writeExpectedSHA256,
                policyVersion: policyVersion
            )
            output = "status=\(response.status)\napproval=\(response.approvalID ?? writeApprovalID)\ntask=\(response.taskID ?? "unknown")\nevidence=\(response.evidenceRefs.joined(separator: ", "))\n\(response.error ?? "signed owner ACK accepted")"
        } catch {
            output = "Signed file-write approval failed: \(error)"
        }
    }

    @MainActor
    private func approveShell() async {
        busy = true
        defer { busy = false }
        do {
            let response = try await client.approveShell(
                approvalID,
                commandSHA256: commandSHA256,
                policyVersion: policyVersion
            )
            output = "status=\(response.status)\napproval=\(response.approvalID ?? approvalID)\ntask=\(response.taskID ?? "unknown")\nevidence=\(response.evidenceRefs.joined(separator: ", "))\n\(response.error ?? "signed owner ACK accepted")"
        } catch {
            output = "Signed approval failed: \(error)"
        }
    }

    @MainActor
    private func readFile() async {
        busy = true
        defer { busy = false }
        do {
            let response = try await client.readFile(path)
            output = "status=\(response.status)\ndecision=\(response.decision)\ntask=\(response.taskID)\nevidence=\(response.evidenceRefs.joined(separator: ", "))\nsha256=\(response.verification.sha256)\n\(response.result?.content ?? response.error ?? "no result")"
        } catch {
            output = "Request failed: \(error)"
        }
    }
}

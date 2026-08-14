import CryptoKit
import Foundation

public enum SovereignNodeError: Error {
    case invalidResponse
    case httpError(Int, Data)
    case signingFailed
}

public struct NodeIntent: Codable, Sendable {
    public let type: String
    public let objective: String
    public let target: [String: String]
    public let requestedCapabilities: [String]

    public init(type: String, objective: String, target: [String: String], requestedCapabilities: [String]) {
        self.type = type
        self.objective = objective
        self.target = target
        self.requestedCapabilities = requestedCapabilities
    }

    enum CodingKeys: String, CodingKey {
        case type
        case objective
        case target
        case requestedCapabilities = "requested_capabilities"
    }
}

public struct FileReadResult: Codable, Sendable {
    public let path: String
    public let size: Int
    public let sha256: String
    public let encoding: String
    public let content: String
}

public struct VestaChatResult: Codable, Sendable {
    public let ok: Bool
    public let bindID: String
    public let taskID: String
    public let evidenceRefs: [String]
    public let decision: String
    public let policyVersion: String
    public let payload: [String: String]
    public let error: String?
    public let auditEventIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case ok
        case bindID = "bind_id"
        case taskID = "task_id"
        case evidenceRefs = "evidence_refs"
        case decision
        case policyVersion = "policy_version"
        case payload
        case error
        case auditEventIDs = "audit_event_ids"
    }
}

public struct VestaReadVerification: Codable, Sendable {
    public let ok: Bool
    public let method: String
    public let sha256: String
}

public struct VestaFileReadResponse: Codable, Sendable {
    public let status: String
    public let bindID: String
    public let taskID: String
    public let evidenceRefs: [String]
    public let decision: String
    public let policyVersion: String
    public let result: FileReadResult?
    public let verification: VestaReadVerification
    public let error: String?
    public let auditEventIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case status
        case bindID = "bind_id"
        case taskID = "task_id"
        case evidenceRefs = "evidence_refs"
        case decision
        case policyVersion = "policy_version"
        case result
        case verification
        case error
        case auditEventIDs = "audit_event_ids"
    }
}

public struct VestaFileWriteApprovalReceipt: Codable, Sendable {
    public let status: String
    public let taskID: String?
    public let approvalID: String?
    public let evidenceRefs: [String]
    public let error: String?
    public let auditEventIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case status
        case taskID = "task_id"
        case approvalID = "approval_id"
        case evidenceRefs = "evidence_refs"
        case error
        case auditEventIDs = "audit_event_ids"
    }
}

public struct VestaShellApprovalReceipt: Codable, Sendable {
    public let status: String
    public let taskID: String?
    public let approvalID: String?
    public let evidenceRefs: [String]
    public let error: String?
    public let auditEventIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case status
        case taskID = "task_id"
        case approvalID = "approval_id"
        case evidenceRefs = "evidence_refs"
        case error
        case auditEventIDs = "audit_event_ids"
    }
}

public struct NodeEngageResponse: Codable, Sendable {
    public let requestID: String?
    public let status: String
    public let executionID: String?
    public let decision: String?
    public let riskLevel: String?
    public let approvalID: String?
    public let result: FileReadResult?
    public let verification: [String: String]?
    public let error: String?
    public let auditEventIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case status
        case executionID = "execution_id"
        case decision
        case riskLevel = "risk_level"
        case approvalID = "approval_id"
        case result
        case verification
        case error
        case auditEventIDs = "audit_event_ids"
    }
}

public struct VestaStatus: Codable, Sendable {
    public let service: String
    public let status: String
    public let mode: String
    public let msbVersion: String
    public let msbReady: Bool
    public let policyVersion: String
    public let transportRequired: Bool
    public let transportAllowedCIDRs: [String]
    public let taskLifecycle: String

    enum CodingKeys: String, CodingKey {
        case service
        case status
        case mode
        case msbVersion = "msb_version"
        case msbReady = "msb_ready"
        case policyVersion = "policy_version"
        case transportRequired = "transport_required"
        case transportAllowedCIDRs = "transport_allowed_cidrs"
        case taskLifecycle = "task_lifecycle"
    }
}

public struct NodeStatusResponse: Codable, Sendable {
    public let node: String
    public let status: String
    public let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case node
        case status
        case protocolVersion = "protocol"
    }
}

public struct VestaTaskSummary: Codable, Sendable {
    public let taskID: String
    public let bindID: String
    public let state: String
    public let lastError: String?

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case bindID = "bind_id"
        case state
        case lastError = "last_error"
    }
}

private struct EnrollmentResponse: Codable {
    let deviceID: String
    let status: String
    let hardwareAssurance: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case status
        case hardwareAssurance = "hardware_assurance"
    }
}

private struct ChallengeResponse: Codable {
    let deviceID: String
    let challenge: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case challenge
    }
}

private struct SessionResponse: Codable {
    let sessionID: String
    let deviceID: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case deviceID = "device_id"
        case expiresAt = "expires_at"
    }
}

private struct EnrollmentRequest: Codable {
    let deviceID: String
    let publicKey: String
    let pairingCode: String
    let hardwareAssurance: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case publicKey = "public_key"
        case pairingCode = "pairing_code"
        case hardwareAssurance = "hardware_assurance"
    }
}

private struct ChallengeRequest: Codable {
    let deviceID: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
    }
}

private struct SessionRequest: Codable {
    let deviceID: String
    let challenge: String
    let signature: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case challenge
        case signature
    }
}

private struct SessionSignaturePayload: Codable {
    let challenge: String
    let deviceID: String
    let `protocol`: String

    enum CodingKeys: String, CodingKey {
        case challenge
        case deviceID = "device_id"
        case `protocol`
    }
}

private struct SignedEngageRequest: Codable {
    let intent: NodeIntent
    let nonce: String
    let requestID: String
    let sessionID: String
    let signature: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case intent
        case nonce
        case requestID = "request_id"
        case sessionID = "session_id"
        case signature
        case timestamp
    }
}

private struct UnsignedEngagePayload: Codable {
    let intent: NodeIntent
    let nonce: String
    let requestID: String
    let sessionID: String
    let timestamp: String
    let `protocol`: String

    enum CodingKeys: String, CodingKey {
        case intent
        case nonce
        case requestID = "request_id"
        case sessionID = "session_id"
        case timestamp
        case `protocol`
    }
}

public final class SovereignNodeClient: @unchecked Sendable {
    public let deviceID: String
    public let keyAssurance: NodeKeyAssurance
    private let signer: any NodeSigner
    private let baseURL: URL
    private let urlSession: URLSession
    private var sessionID: String?

    public init(deviceID: String, baseURL: URL, privateKey: P256.Signing.PrivateKey = .init(), urlSession: URLSession = .shared) {
        self.deviceID = deviceID
        self.baseURL = baseURL
        self.signer = SoftwareNodeSigner(key: privateKey)
        self.keyAssurance = .software
        self.urlSession = urlSession
    }

    public init(deviceID: String, baseURL: URL, keyStore: NodeKeyStore, urlSession: URLSession = .shared) throws {
        let material = try keyStore.loadOrCreate()
        self.deviceID = deviceID
        self.baseURL = baseURL
        self.signer = material.signer
        self.keyAssurance = material.assurance
        self.urlSession = urlSession
    }

    public var publicKeyBase64URL: String {
        Self.base64URL(signer.publicKeyX963)
    }

    public func enroll(pairingCode: String, hardwareAssurance: String = "software") async throws {
        let body = EnrollmentRequest(
            deviceID: deviceID,
            publicKey: publicKeyBase64URL,
            pairingCode: pairingCode,
            hardwareAssurance: hardwareAssurance
        )
        _ = try await post("/auth/enroll", body: body, decode: EnrollmentResponse.self)
    }

    public func authenticate() async throws {
        let challenge = try await post(
            "/auth/challenge",
            body: ChallengeRequest(deviceID: deviceID),
            decode: ChallengeResponse.self
        )
        let payload = SessionSignaturePayload(
            challenge: challenge.challenge,
            deviceID: deviceID,
            protocol: "node.v1"
        )
        let signature = try sign(Self.canonicalJSON(payload))
        let session = try await post(
            "/auth/session",
            body: SessionRequest(deviceID: deviceID, challenge: challenge.challenge, signature: signature),
            decode: SessionResponse.self
        )
        sessionID = session.sessionID
    }

    public func chat(_ query: String) async throws -> VestaChatResult {
        guard let sessionID else { throw SovereignNodeError.invalidResponse }
        let requestID = UUID().uuidString.lowercased()
        let nonce = UUID().uuidString + UUID().uuidString
        let timestamp = ISO8601DateFormatter().string(from: Date())
        let intent = NodeIntent(
            type: "chat",
            objective: query,
            target: ["query": query],
            requestedCapabilities: ["model.inference", "memory.read"]
        )
        let unsigned = UnsignedEngagePayload(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            timestamp: timestamp,
            protocol: "node.v1"
        )
        let signature = try sign(Self.canonicalJSON(unsigned))
        let body = SignedEngageRequest(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            signature: signature,
            timestamp: timestamp
        )
        return try await post(
            "/vesta/signed-chat",
            body: body,
            decode: VestaChatResult.self,
            prefix: ""
        )
    }

    public func vestaStatus() async throws -> VestaStatus {
        try await get("/vesta/status", decode: VestaStatus.self, prefix: "")
    }

    public func nodeStatus() async throws -> NodeStatusResponse {
        try await get("/status", decode: NodeStatusResponse.self, prefix: "node/v1")
    }

    public func task(_ taskID: String) async throws -> VestaTaskSummary {
        try await get("/vesta/tasks/\(taskID)", decode: VestaTaskSummary.self, prefix: "")
    }

    public func approveFileWrite(
        _ approvalID: String,
        targetPath: String,
        payloadSHA256: String,
        expectedSHA256: String = "",
        policyVersion: String = "vesta-policy-1"
    ) async throws -> VestaFileWriteApprovalReceipt {
        guard let sessionID else { throw SovereignNodeError.invalidResponse }
        let requestID = UUID().uuidString.lowercased()
        let nonce = UUID().uuidString + UUID().uuidString
        let timestamp = ISO8601DateFormatter().string(from: Date())
        let intent = NodeIntent(
            type: "file_write_approval",
            objective: "Approve the exact file-write contract",
            target: [
                "approval_id": approvalID,
                "target_path": targetPath,
                "payload_sha256": payloadSHA256,
                "expected_sha256": expectedSHA256,
                "policy_version": policyVersion
            ],
            requestedCapabilities: ["human.request_ack"]
        )
        let unsigned = UnsignedEngagePayload(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            timestamp: timestamp,
            protocol: "node.v1"
        )
        let signature = try sign(Self.canonicalJSON(unsigned))
        let body = SignedEngageRequest(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            signature: signature,
            timestamp: timestamp
        )
        return try await post(
            "/vesta/approvals/\(approvalID)/signed-approve",
            body: body,
            decode: VestaFileWriteApprovalReceipt.self,
            prefix: ""
        )
    }

    public func approveShell(
        _ approvalID: String,
        commandSHA256: String,
        policyVersion: String = "vesta-policy-1"
    ) async throws -> VestaShellApprovalReceipt {
        guard let sessionID else { throw SovereignNodeError.invalidResponse }
        let requestID = UUID().uuidString.lowercased()
        let nonce = UUID().uuidString + UUID().uuidString
        let timestamp = ISO8601DateFormatter().string(from: Date())
        let intent = NodeIntent(
            type: "shell_approval",
            objective: "Approve the exact shell contract",
            target: [
                "approval_id": approvalID,
                "command_sha256": commandSHA256,
                "policy_version": policyVersion
            ],
            requestedCapabilities: ["human.request_ack"]
        )
        let unsigned = UnsignedEngagePayload(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            timestamp: timestamp,
            protocol: "node.v1"
        )
        let signature = try sign(Self.canonicalJSON(unsigned))
        let body = SignedEngageRequest(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            signature: signature,
            timestamp: timestamp
        )
        return try await post(
            "/vesta/shell/approvals/\(approvalID)/signed-approve",
            body: body,
            decode: VestaShellApprovalReceipt.self,
            prefix: ""
        )
    }

    public func readFile(_ path: String) async throws -> VestaFileReadResponse {
        guard let sessionID else { throw SovereignNodeError.invalidResponse }
        let requestID = UUID().uuidString.lowercased()
        let nonce = UUID().uuidString + UUID().uuidString
        let timestamp = ISO8601DateFormatter().string(from: Date())
        let intent = NodeIntent(
            type: "read_file",
            objective: "Read \(path)",
            target: ["path": path],
            requestedCapabilities: ["filesystem.read"]
        )
        let unsigned = UnsignedEngagePayload(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            timestamp: timestamp,
            protocol: "node.v1"
        )
        let signature = try sign(Self.canonicalJSON(unsigned))
        let body = SignedEngageRequest(
            intent: intent,
            nonce: nonce,
            requestID: requestID,
            sessionID: sessionID,
            signature: signature,
            timestamp: timestamp
        )
        return try await post(
            "/vesta/signed-read",
            body: body,
            decode: VestaFileReadResponse.self,
            prefix: ""
        )
    }

    private func sign(_ data: Data) throws -> String {
        do {
            return Self.base64URL(try signer.sign(data))
        } catch {
            throw SovereignNodeError.signingFailed
        }
    }

    private func get<Response: Decodable>(
        _ path: String,
        decode: Response.Type,
        prefix: String
    ) async throws -> Response {
        let route = "\(prefix)\(path)".trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let request = URLRequest(url: baseURL.appendingPathComponent(route))
        let (data, response) = try await urlSession.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw SovereignNodeError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw SovereignNodeError.httpError(http.statusCode, data)
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func post<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        decode: Response.Type,
        prefix: String = "node/v1"
    ) async throws -> Response {
        let route = "\(prefix)\(path)".trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        var request = URLRequest(url: baseURL.appendingPathComponent(route))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try Self.canonicalJSON(body)
        let (data, response) = try await urlSession.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw SovereignNodeError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw SovereignNodeError.httpError(http.statusCode, data)
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private static func canonicalJSON<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(value)
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .trimmingCharacters(in: CharacterSet(charactersIn: "="))
    }
}

import XCTest
@testable import SovereignNodeClient

final class SovereignNodeClientTests: XCTestCase {
    func testClientUsesP256X963PublicKey() {
        let client = SovereignNodeClient(
            deviceID: "test-device",
            baseURL: URL(string: "http://127.0.0.1:8766")!
        )
        let publicKey = Data(base64URL: client.publicKeyBase64URL)
        XCTAssertEqual(publicKey.count, 65)
        XCTAssertEqual(publicKey.first, 4)
        XCTAssertEqual(client.keyAssurance, .software)
    }

    func testVestaStatusAndTaskReceiptsDecode() throws {
        let status = try JSONDecoder().decode(
            VestaStatus.self,
            from: Data("""
            {"service":"vesta","status":"ACTIVE","mode":"phase-0-2","msb_version":"0.2.3","msb_ready":true,"policy_version":"vesta-policy-1","transport_required":false,"transport_allowed_cidrs":["127.0.0.1/32"],"task_lifecycle":"durable-sqlite"}
            """.utf8)
        )
        XCTAssertEqual(status.status, "ACTIVE")
        XCTAssertFalse(status.transportRequired)

        let task = try JSONDecoder().decode(
            VestaTaskSummary.self,
            from: Data("""
            {"task_id":"task_fixture","bind_id":"bind_fixture","state":"COMPLETED","last_error":null}
            """.utf8)
        )
        XCTAssertEqual(task.taskID, "task_fixture")
        XCTAssertEqual(task.state, "COMPLETED")
    }

    func testVestaReadReceiptDecodesEvidenceAndVerification() throws {
        let response = try JSONDecoder().decode(
            VestaFileReadResponse.self,
            from: Data("""
            {"status":"completed","bind_id":"bind_read","task_id":"task_read","evidence_refs":["ev_request","ev_content"],"decision":"ALLOW","policy_version":"vesta-policy-1","result":{"path":"hello.txt","size":5,"sha256":"abc","encoding":"utf-8","content":"hello"},"verification":{"ok":true,"method":"file_exists_size_and_sha256","sha256":"abc"},"error":null,"audit_event_ids":[1,2]}
            """.utf8)
        )
        XCTAssertEqual(response.status, "completed")
        XCTAssertEqual(response.result?.content, "hello")
        XCTAssertTrue(response.verification.ok)
        XCTAssertEqual(response.evidenceRefs.count, 2)
    }

    func testSignedFileWriteApprovalReceiptDecodes() throws {
        let receipt = try JSONDecoder().decode(
            VestaFileWriteApprovalReceipt.self,
            from: Data("""
            {"status":"completed","approval_id":"ack_fixture","task_id":"task_write_fixture","evidence_refs":["ev_request","ev_payload","ev_receipt"],"audit_event_ids":[20,21]}
            """.utf8)
        )
        XCTAssertEqual(receipt.status, "completed")
        XCTAssertEqual(receipt.approvalID, "ack_fixture")
        XCTAssertEqual(receipt.taskID, "task_write_fixture")
        XCTAssertEqual(receipt.evidenceRefs.count, 3)
    }

    func testSignedShellApprovalReceiptDecodes() throws {
        let receipt = try JSONDecoder().decode(
            VestaShellApprovalReceipt.self,
            from: Data("""
            {"status":"completed","approval_id":"shell_ack_fixture","task_id":"task_shell_fixture","evidence_refs":["ev_request","ev_output"],"audit_event_ids":[10,11],"execution":{"stdout":"OK\\n","returncode":0},"verification":{"ok":true}}
            """.utf8)
        )
        XCTAssertEqual(receipt.status, "completed")
        XCTAssertEqual(receipt.approvalID, "shell_ack_fixture")
        XCTAssertEqual(receipt.taskID, "task_shell_fixture")
        XCTAssertEqual(receipt.evidenceRefs.count, 2)
    }

    func testKeyStorePersistsKeychainIdentity() throws {
        let service = "com.lordwilson.sovereign-node.tests"
        let account = UUID().uuidString
        let store = NodeKeyStore(service: service, account: account)
        defer { try? store.remove() }

        let first = try store.loadOrCreate()
        let second = try store.loadOrCreate()

        XCTAssertEqual(first.assurance, .keychain)
        XCTAssertEqual(first.signer.publicKeyX963, second.signer.publicKeyX963)
        XCTAssertEqual(try first.signer.sign(Data("fixture".utf8)).count, 64)
        XCTAssertEqual(try second.signer.sign(Data("fixture".utf8)).count, 64)
    }
}

private extension Data {
    init(base64URL value: String) {
        var padded = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        padded += String(repeating: "=", count: (4 - padded.count % 4) % 4)
        self.init(base64Encoded: padded)!
    }
}

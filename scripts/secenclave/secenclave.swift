// secenclave-tool — P-256 Secure Enclave key operations for MSB's chain anchor.
//
// The anchor's proof is only as strong as the key that signs it. The software
// seed (MSB_CHAIN_ANCHOR_KEY / keyfile) is a file an attacker who owns the
// box can read and copy. A key created inside Apple's Secure Enclave can
// never be exported — signing happens inside the enclave, so a box compromise
// cannot forge fresh anchors (security-hardening #1).
//
// The private key never leaves the enclave. Access control is
// kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly + kSecAccessControlPrivateKeyUsage:
// the key is usable without an interactive prompt after the operator has
// unlocked the Mac once following boot — required for the unattended launchd
// notary/verify jobs — while remaining non-exportable.
//
// Wire contract (JSON on stdout, exit 0 = ok, 1 = error):
//   create  --label L [--force]  -> {"ok":true,"public_key":"04<X><Y>","label":L}
//   public  --label L            -> {"ok":true,"public_key":"04<X><Y>","label":L}
//   sign    --label L --hex M    -> {"ok":true,"signature":"<64-byte X9.62 r||s hex>"}
//   delete  --label L            -> {"ok":true,"deleted":L}
//
// The signature is the raw X9.62 r||s encoding (64 bytes for P-256); the
// Python side converts it to DER (uac/signing.py `_x962_to_der`) so the
// existing algorithm-agnostic verifier can check it.
//
// Build: scripts/secenclave/build.sh   (macOS only)

import Foundation
import Security

func fail(_ message: String) -> Never {
    let payload = ["ok": "false", "error": message]
    let data = try! JSONSerialization.data(withJSONObject: payload)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    exit(1)
}

func ok(_ dict: [String: String]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: dict)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    exit(0)
}

func keyQuery(label: String) -> [String: Any] {
    return [
        kSecClass as String: kSecClassKey,
        kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecAttrApplicationLabel as String: label.data(using: .utf8)!,
    ]
}

func findKey(label: String) -> SecKey {
    var query = keyQuery(label: label)
    query[kSecReturnRef as String] = true
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    guard status == errSecSuccess, let item = item, CFGetTypeID(item) == SecKeyGetTypeID() else {
        fail("no Secure Enclave key labeled '\(label)' (enroll with: secenclave-tool create --label \(label))")
    }
    return item as! SecKey  // type checked via CFGetTypeID above
}

func publicKeyHex(_ key: SecKey) -> String {
    var error: Unmanaged<CFError>?
    guard let pub = SecKeyCopyPublicKey(key) else {
        fail("could not extract public key")
    }
    guard let data = SecKeyCopyExternalRepresentation(pub, &error) as Data? else {
        fail("could not export public key: \(error?.takeRetainedValue().localizedDescription ?? "unknown")")
    }
    return data.map { String(format: "%02x", $0) }.joined()
}

func createKey(label: String, force: Bool) {
    if force {
        SecItemDelete(keyQuery(label: label) as CFDictionary)
    } else {
        var query = keyQuery(label: label)
        query[kSecReturnRef as String] = true
        var item: CFTypeRef?
        if SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess {
            fail("key '\(label)' already exists (use --force to re-create)")
        }
    }
    var acError: Unmanaged<CFError>?
    guard let access = SecAccessControlCreateWithFlags(
        kCFAllocatorDefault,
        kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        .privateKeyUsage,
        &acError
    ) else {
        fail("could not create key access control: \(acError?.takeRetainedValue().localizedDescription ?? "unknown")")
    }
    let attributes: [String: Any] = [
        kSecClass as String: kSecClassKey,
        kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecAttrKeySizeInBits as String: 256,
        kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
        kSecAttrApplicationLabel as String: label.data(using: .utf8)!,
        kSecAttrLabel as String: label,
        kSecAttrAccessControl as String: access,
    ]
    var error: Unmanaged<CFError>?
    guard let key = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else {
        fail("Secure Enclave key creation failed: \(error?.takeRetainedValue().localizedDescription ?? "unknown")")
    }
    ok(["ok": "true", "public_key": publicKeyHex(key), "label": label])
}

func publicKey(label: String) {
    let key = findKey(label: label)
    ok(["ok": "true", "public_key": publicKeyHex(key), "label": label])
}

func sign(label: String, hex: String) {
    guard let message = Data(hexString: hex) else {
        fail("invalid --hex value")
    }
    let key = findKey(label: label)
    var error: Unmanaged<CFError>?
    guard let sig = SecKeyCreateSignature(
        key, .ecdsaSignatureMessageX962SHA256, message as CFData, &error
    ) as Data? else {
        fail("Secure Enclave signing failed: \(error?.takeRetainedValue().localizedDescription ?? "unknown")")
    }
    ok(["ok": "true", "signature": sig.map { String(format: "%02x", $0) }.joined(), "label": label])
}

func deleteKey(label: String) {
    let status = SecItemDelete(keyQuery(label: label) as CFDictionary)
    if status == errSecSuccess || status == errSecItemNotFound {
        ok(["ok": "true", "deleted": label])
    } else {
        fail("delete failed with OSStatus \(status)")
    }
}

extension Data {
    init?(hexString: String) {
        let s = hexString.count % 2 == 0 ? hexString : "0" + hexString
        var bytes = [UInt8]()
        var i = s.startIndex
        while i < s.endIndex {
            let next = s.index(i, offsetBy: 2)
            guard let b = UInt8(s[i..<next], radix: 16) else { return nil }
            bytes.append(b)
            i = next
        }
        self = bytes.withUnsafeBytes { Data($0) }
    }
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    fail("usage: secenclave-tool <create|public|sign|delete> [--label L] [--hex H] [--force]")
}
let command = args[1]
var label = "msb-chain-anchor"
var hex = ""
var force = false
var i = 2
while i < args.count {
    switch args[i] {
    case "--label":
        if i + 1 < args.count { label = args[i + 1]; i += 1 }
    case "--hex":
        if i + 1 < args.count { hex = args[i + 1]; i += 1 }
    case "--force":
        force = true
    default:
        break
    }
    i += 1
}
switch command {
case "create": createKey(label: label, force: force)
case "public": publicKey(label: label)
case "sign": sign(label: label, hex: hex)
case "delete": deleteKey(label: label)
default: fail("unknown command: \(command)")
}

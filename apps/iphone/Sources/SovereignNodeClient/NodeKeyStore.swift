import CryptoKit
import Foundation
import Security

public enum NodeKeyAssurance: String, Codable, Sendable {
    case secureEnclave = "secure_enclave"
    case keychain
    case software
}

public enum NodeKeyStoreError: Error, Equatable {
    case keychain(OSStatus)
    case invalidStoredKey
    case secureEnclaveUnavailable
}

public protocol NodeSigner: Sendable {
    var publicKeyX963: Data { get }
    func sign(_ data: Data) throws -> Data
}

public struct SoftwareNodeSigner: NodeSigner, Sendable {
    public let key: P256.Signing.PrivateKey

    public init(key: P256.Signing.PrivateKey = .init()) {
        self.key = key
    }

    public var publicKeyX963: Data { key.publicKey.x963Representation }

    public func sign(_ data: Data) throws -> Data {
        try key.signature(for: data).rawRepresentation
    }
}

#if os(iOS)
public struct SecureEnclaveNodeSigner: NodeSigner, Sendable {
    private let key: SecureEnclave.P256.Signing.PrivateKey

    public init(key: SecureEnclave.P256.Signing.PrivateKey) {
        self.key = key
    }

    public var publicKeyX963: Data { key.publicKey.x963Representation }

    public func sign(_ data: Data) throws -> Data {
        try key.signature(for: data).rawRepresentation
    }
}
#endif

public struct NodeKeyMaterial: Sendable {
    public let signer: any NodeSigner
    public let assurance: NodeKeyAssurance

    public init(signer: any NodeSigner, assurance: NodeKeyAssurance) {
        self.signer = signer
        self.assurance = assurance
    }
}

public final class NodeKeyStore: @unchecked Sendable {
    private static let secureEnclaveVersion: UInt8 = 2
    private static let keychainVersion: UInt8 = 1

    private let service: String
    private let account: String

    public init(service: String = "com.lordwilson.sovereign-node", account: String) {
        self.service = service
        self.account = account
    }

    public func loadOrCreate() throws -> NodeKeyMaterial {
        if let stored = try load() {
            return try material(from: stored)
        }

#if os(iOS)
        if SecureEnclave.isAvailable {
            let key = try SecureEnclave.P256.Signing.PrivateKey()
            try save(version: Self.secureEnclaveVersion, representation: key.dataRepresentation)
            return NodeKeyMaterial(signer: SecureEnclaveNodeSigner(key: key), assurance: .secureEnclave)
        }
#endif

        let key = P256.Signing.PrivateKey()
        try save(version: Self.keychainVersion, representation: key.rawRepresentation)
        return NodeKeyMaterial(signer: SoftwareNodeSigner(key: key), assurance: .keychain)
    }

    public func remove() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NodeKeyStoreError.keychain(status)
        }
    }

    private func load() throws -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = result as? Data else {
            throw NodeKeyStoreError.keychain(status)
        }
        return data
    }

    private func save(version: UInt8, representation: Data) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: Data([version]) + representation,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var item = query
            item.merge(attributes) { _, new in new }
            let addStatus = SecItemAdd(item as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw NodeKeyStoreError.keychain(addStatus)
            }
        } else if updateStatus != errSecSuccess {
            throw NodeKeyStoreError.keychain(updateStatus)
        }
    }

    private func material(from stored: Data) throws -> NodeKeyMaterial {
        guard let version = stored.first else {
            throw NodeKeyStoreError.invalidStoredKey
        }
        let representation = stored.dropFirst()
        switch version {
        case Self.keychainVersion:
            do {
                let key = try P256.Signing.PrivateKey(rawRepresentation: Data(representation))
                return NodeKeyMaterial(signer: SoftwareNodeSigner(key: key), assurance: .keychain)
            } catch {
                throw NodeKeyStoreError.invalidStoredKey
            }
        case Self.secureEnclaveVersion:
#if os(iOS)
            do {
                let key = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: Data(representation))
                return NodeKeyMaterial(signer: SecureEnclaveNodeSigner(key: key), assurance: .secureEnclave)
            } catch {
                throw NodeKeyStoreError.invalidStoredKey
            }
#else
            throw NodeKeyStoreError.secureEnclaveUnavailable
#endif
        default:
            throw NodeKeyStoreError.invalidStoredKey
        }
    }
}

// KeychainTool — profile-minting vehicle only.
//
// This app is never run. Its only job is to exist as an Xcode target with
// automatic signing + the Keychain Sharing capability, so Xcode generates and
// embeds a development provisioning profile that authorizes the
// `keychain-access-groups` entitlement on this device. `enroll.sh` then wraps
// the real `secenclave-tool` binary in an app bundle carrying this profile and
// codesigns it with the same identity — the only way macOS allows a CLI
// process to persist a Secure Enclave key (see
// docs/operations/secure-enclave-anchor.md, "The macOS entitlement wall").
import SwiftUI

@main
struct KeychainToolApp: App {
    var body: some Scene {
        WindowGroup {
            Text("MSB Secure Enclave profile vehicle — not meant to be run")
                .padding(24)
        }
    }
}

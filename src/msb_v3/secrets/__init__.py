"""Secret handling — one broker seam over *where* a secret lives, one redactor.

Import from the package, not the submodules, so the seam has a single front
door::

    from msb_v3.secrets import SecretRef, redact, redact_obj, register_secret_value

The two halves are deliberately separate: ``broker`` knows where a secret comes
from and hands out a value that resists being printed; ``redact`` knows how to
mask one on the way out, and is what every output channel calls.
"""

from msb_v3.secrets.broker import (
    SEED_ENV_NAMES,
    BrokerSpec,
    EnvSecretBroker,
    KeychainSecretBroker,
    Secret,
    SecretBroker,
    SecretBrokerRegistry,
    SecretRef,
    SecretUnavailable,
    UnavailableSecretBroker,
    default_registry,
    seed_redaction_from_env,
)
from msb_v3.secrets.redact import (
    MASK,
    clear_registry,
    contains_secret,
    redact,
    redact_obj,
    register_secret_value,
    registered_values,
)
from msb_v3.secrets.selfcheck import (
    ALLOW_ENV_FLAG,
    EXPECT_ENV_FLAG,
    RedactionStatus,
    SecretRedactionUnarmed,
    redaction_self_check,
)

__all__ = [
    "ALLOW_ENV_FLAG",
    "EXPECT_ENV_FLAG",
    "MASK",
    "RedactionStatus",
    "SEED_ENV_NAMES",
    "BrokerSpec",
    "EnvSecretBroker",
    "KeychainSecretBroker",
    "Secret",
    "SecretBroker",
    "SecretBrokerRegistry",
    "SecretRedactionUnarmed",
    "SecretRef",
    "SecretUnavailable",
    "UnavailableSecretBroker",
    "clear_registry",
    "contains_secret",
    "default_registry",
    "redact",
    "redact_obj",
    "register_secret_value",
    "redaction_self_check",
    "registered_values",
    "seed_redaction_from_env",
]

from serializable_context_main_objects.data.vault import VaultSecretReference

from pydantic import BaseModel

from typing import Literal
from enum import Enum


class EncryptionPolicyType(Enum):
    NO_ENCRYPTION = 1
    ENCRYPTED_WITH_PLAINTEXT_PARAMETERS = 2  # not really safe API, use it in case of no vault available nor upper layer
    ENCRYPTED_WITH_UPPER_LAYER = 3     # ex: DPAPI, K8S secret
    ENCRYPTED_WITH_EXTERNAL_VAULT = 4  # will use workload identity to unlock secret, best method

class EncryptionPolicyInstance(BaseModel):
    encryptionType: EncryptionPolicyType

class NoEncryptionPolicyInstance(EncryptionPolicyInstance):
    encryptionType: Literal[EncryptionPolicyType.NO_ENCRYPTION] = EncryptionPolicyType.NO_ENCRYPTION

class WithEncryptionPolicyInstance(EncryptionPolicyInstance):
    encryptionAlgorithm: EncryptionAlgorithmInstance

class WithPlaintextEncryptionPolicyInstance(WithEncryptionPolicyInstance):
    encryptionType: Literal[EncryptionPolicyType.ENCRYPTED_WITH_PLAINTEXT_PARAMETERS] = EncryptionPolicyType.ENCRYPTED_WITH_PLAINTEXT_PARAMETERS
    plaintextParameters: T

class UpperLayerEncryptionInstanceType(Enum):
    DPAPI = 1
    K8S_SECRET = 2

class UpperLayerEncryptionInstance(BaseModel):
    upperLayerEncryptionInstanceType: UpperLayerEncryptionInstanceType

class WithUpperLayerEncryptionPolicyInstance(WithEncryptionPolicyInstance):
    encryptionType: Literal[EncryptionPolicyType.ENCRYPTED_WITH_UPPER_LAYER] = EncryptionPolicyType.ENCRYPTED_WITH_UPPER_LAYER
    upperLayerEncryption: UpperLayerEncryptionInstance

class WithExternalVaultEncryptionPolicyInstance(WithEncryptionPolicyInstance):
    encryptionType: Literal[EncryptionPolicyType.ENCRYPTED_WITH_EXTERNAL_VAULT] = EncryptionPolicyType.ENCRYPTED_WITH_EXTERNAL_VAULT
    vaultSecretReference: VaultSecretReference


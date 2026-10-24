from enum import Enum
from typing import Literal

from pydantic import BaseModel, FilePath

from serializable_context_main_objects.communication.communication_tunnel import CommunicationTunnelTo
from serializable_context_main_objects.data.encrypted_data import EncryptionPolicyInstance


class InternalResourceLocationType(Enum):
    LOCAL_FILE = 1
    LOCAL_DB = 2
    REMOTE_DB = 3

class InternalResourceLocation(BaseModel):
    locationType: InternalResourceLocationType

class LocalFsResource(InternalResourceLocation):
    encryptionPolicy: EncryptionPolicyInstance
    filePath: FilePath

class LocalFile(LocalFsResource):
    locationType: Literal[InternalResourceLocationType.LOCAL_FILE] = InternalResourceLocationType.LOCAL_FILE

class LocationInDatabase(BaseModel):
    schema: str | None
    database: str | None
    table: str | None

class LocalDatabase(LocalFsResource, LocationInDatabase):
    locationType: Literal[InternalResourceLocationType.LOCAL_DB] = InternalResourceLocationType.LOCAL_DB

class NetworkDatabase(InternalResourceLocation, LocationInDatabase):
    locationType: Literal[InternalResourceLocationType.REMOTE_DB] = InternalResourceLocationType.REMOTE_DB
    databaseLocation: CommunicationTunnelTo


# session controller on any compute is able to resolved contextIdentifier and to match it against its current environment
# so that creating a session with referenced context, if context is valid, should be the same
class ExternalDomainResourceLocation(BaseModel):
    computeThatCreatedContext: ComputeReferenceInDomain
    contextIdentifier: ContextIdentifierForCompute

from __future__ import annotations

from serializable_context_main_objects.communication.communication_tunnel import CommunicationTunnelTo

from pydantic import BaseModel
from ulid import ULID

from typing import TypeVar, Generic


PublicAuth = TypeVar('PublicAuth')
BoundaryVerifier = TypeVar('BoundaryVerifier')

class BoundaryIdentity(BaseModel, Generic[PublicAuth]):
    name: str
    identifier: ULID
    publicAuthenticationMaterial: PublicAuth

class SignedBoundaryIdentity(BaseModel, Generic[PublicAuth, BoundaryVerifier]):
    identity: BoundaryIdentity[PublicAuth]
    signature: BoundaryVerifier
    signerReference: BoundaryIdentity[PublicAuth]


class BasicIdentityReference(BaseModel):
    identifier: int  # main identifier that is unique trust source in everything within current domain
    humanAlias: str | None = None  # optional human alias to ease identity read

# the class below represent a full identity reference, sufficient to identify any identity within a given domain
class CompleteIdentityReference(BasicIdentityReference, Generic[PublicAuth]):
    forest: BoundaryIdentity[PublicAuth] | None = None  # implicit same forest by default
    domain: BoundaryIdentity[PublicAuth] | None = None  # implicit same domain by default

# one can ask DC the identity provider which will handle the identifier, otherwise more complete identities can
# cache the information and avoid permanent retrieval
class FullIdentityReference(CompleteIdentityReference[PublicAuth]):
    identityProviderLocation: CommunicationTunnelTo
    identityProviderIdentity: CompleteIdentityReference | FullIdentityReference

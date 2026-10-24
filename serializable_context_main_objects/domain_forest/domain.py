from serializable_context_main_objects.authority.identity import SignedBoundaryIdentity, BoundaryIdentity

from pydantic import BaseModel

from typing import Generic, TypeVar


PublicAuth = TypeVar('PublicAuth')
BoundaryVerifier = TypeVar('BoundaryVerifier')


# this is really a big struct to keep as it holds the full chain of trust to the domain_forest that generated the domain_forest that
# generated the domain_forest ... that generated the domain that generated the domain ... that generated current domain
# so this is a structure which is only asked if needed, once, and then the shorter version can be used
class ChainOfLocationFullProof(BaseModel, Generic[PublicAuth, BoundaryVerifier]):
    domainChainToRoot: list[SignedBoundaryIdentity[PublicAuth, BoundaryVerifier]]
    forestChainToEvent0: list[SignedBoundaryIdentity[PublicAuth, BoundaryVerifier]]


class ForestIdentity(BoundaryIdentity[PublicAuth]):
    pass

class DomainIdentity(BoundaryIdentity[PublicAuth]):
    forest: ForestIdentity[PublicAuth]

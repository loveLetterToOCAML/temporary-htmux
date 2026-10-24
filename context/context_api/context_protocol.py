from serializable_context_main_objects.data.resource import InternalResourceLocation, ExternalDomainResourceLocation
from serializable_context_main_objects.authority.identity import BoundaryIdentity, CompleteIdentityReference
from serializable_context_main_objects.communication.communication_tunnel import CommunicationTunnelTo
from serializable_context_main_objects.execution.execution import ExecutionIntent
from serializable_context_main_objects.execution.session import Session

from pydantic import BaseModel

from typing import Protocol, TypeVar, Generic


T = TypeVar('T')

class NamedDict(dict[str, T], Generic[T]):
    pass


class ContextIdentity(BaseModel):
    # internal resource is when within the same execution boundary, while external is beyond
    # this limit can be a little tricky, as some internal database is considered internal location
    root: InternalResourceLocation | ExternalDomainResourceLocation | None
    parent: InternalResourceLocation | ExternalDomainResourceLocation | None
    children: NamedDict[InternalResourceLocation | ExternalDomainResourceLocation] = {}
    state: InternalResourceLocation
    runAs: CompleteIdentityReference
    # location of domain_forest within the universe; default to same info as above (or local root domain_forest if none)
    universeLocation: BoundaryIdentity | None = None
    # location of domain within the domain_forest; default to same info as above (or local root domain if none)
    forestLocation: BoundaryIdentity | None = None
    locationFullProof: InternalResourceLocation | None = None  # if none, we won't be able to get proof of within valid domain/forest by accessing context identity


class ExecutionContext(BaseModel):
    datasources: NamedDict[Datasource]
    configs: NamedDict[InternalResourceLocation | ParsedConfig]
    mustRun: NamedDict[ExecutionIntent]
    externalComponents: NamedDict[ExternalComponent]
    aggregatedConfigs: NamedDict[MergedConfig]
    identities: NamedDict[CompleteIdentityReference]


class InheritableExecutionContext(ExecutionContext):
    domainControllerTunnel: CommunicationTunnelTo
    domainControllerIdentifier: BoundaryIdentity
    nearestSessionControllerTunnel: CommunicationTunnelTo | None = None  # nearest boundary to other computation engines
    nearestSessionControllerIdentifier: BoundaryIdentity | None = None


class NonInheritableExecutionContext(ExecutionContext):
    currentSessions: NamedDict[Session]  # sessions launched from current context, these can be launched starting in other contexts
    stateForSession: NamedDict[State]
    stateForExternalComponent: NamedDict[State]


class ExecutionContext(BaseModel):
    inheritable: InheritableExecutionContext
    nonInheritable: NonInheritableExecutionContext
    identity: ContextIdentity


class ContextApi(Protocol):

    async def new_context(self, name: str):
        ...

    def switch_to_context(self):
        ...
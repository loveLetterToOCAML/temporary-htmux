from enum import Enum
from typing import Literal

from pydantic import BaseModel


class CommunicationTunnelType(Enum):
    OS_LOCAL = 1
    OS_DIRECT_NETWORK = 2
    OS_TUNNELED_NETWORK = 3
    NEAREST_ROUTER = 4

class CommunicationTunnelTo(BaseModel):
    communicationTunnelType: CommunicationTunnelType

class LocalCommunicationTunnelType(Enum):
    LOCAL_TCP = 1
    LRPC = 2
    UNIX_SOCKET = 3
    NO_BOUNDARY_CHANNEL_SAME_PROCESS = 4  # direct function call as the component, needs available state to act as
    NAMED_PIPE = 5
    CUSTOM_ON_EXISTING_KERNEL = 6
    CUSTOM_NEW_KERNEL_MODULE = 7

class LocalCommunicationTunnelTo(BaseModel):
    communicationTunnelType: LocalCommunicationTunnelType

class LocalTCPCommunicationTunnelTo(LocalCommunicationTunnelTo):
    communicationTunnelType: Literal[LocalCommunicationTunnelType.LOCAL_TCP] = LocalCommunicationTunnelType.LOCAL_TCP
    tcpPort: int

class LocalLRPCCommunicationTunnelTo(LocalCommunicationTunnelTo):
    communicationTunnelType: Literal[LocalCommunicationTunnelType.LOCAL_TCP] = LocalCommunicationTunnelType.LOCAL_TCP
    # TODO


class LocalCommunicationTunnelTo(CommunicationTunnelTo):
    communicationTunnelType: Literal[CommunicationTunnelType.OS_LOCAL] = CommunicationTunnelType.OS_LOCAL
    localCanal: LocalCommunicationTunnelTo

class NetworkDirectCommunicationTunnelTo(CommunicationTunnelTo):
    communicationTunnelType: Literal[CommunicationTunnelType.OS_DIRECT_NETWORK] = CommunicationTunnelType.OS_DIRECT_NETWORK
    networkCanal: NetworkCommunicationTunnelTo

class TunneledCommunicationTunnelTo(CommunicationTunnelTo):
    communicationTunnelType: Literal[CommunicationTunnelType.OS_TUNNELED_NETWORK] = CommunicationTunnelType.OS_TUNNELED_NETWORK
    tunneledCanal: TunneledCommunicationTunnelTo

class RoutedCommunicationTunnelTo(CommunicationTunnelTo):
    communicationTunnelType: Literal[CommunicationTunnelType.NEAREST_ROUTER] = CommunicationTunnelType.NEAREST_ROUTER
    routerIdentifier: int | None = None    # if none, won't expect any specific router, only one of same domain
    allowRouterOfOtherDomain: bool = True  # if true, traffic will be allowed when transiting on a router of same domain_forest, not necessarily same domain
    underlyingTransport: LocalCommunicationTunnelTo | NetworkDirectCommunicationTunnelTo | TunneledCommunicationTunnelTo

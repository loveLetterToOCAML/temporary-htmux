from interact.observable_session.color import BaseColor, ColorPalette

from pydantic import BaseModel

from datetime import datetime
from typing import Literal
from enum import Enum

from interact.observable_session.geometry_intents import TwoDirections


# 1 pane = 1 interaction among available ones
# = 1 set of parameters that defines what operations can be done on the pane
# + 1 current state (geom, modal, ...)
# + 1 remote / external link to something "real"

class RemoteDisconnectReactionType(Enum):
    CLOSE_ON_DISCONNECT = 1
    CONFIRM_CLOSE_ON_DISCONNECT = 2
    WAIT_REMOTE_RECONNECT = 3

class RemoteExitReactionType(Enum):
    CLOSE = 1
    CONFIRM_CLOSE = 2
    RESTART = 3
    CONFIRM_RESTART = 4
    RESTART_WITH_EXPONENTIAL_BACKOFF = 5

class RemotePauseReactionType(Enum):
    PAUSE_PANE = 1
    DO_NOTHING = 2


class GeometryParameters(BaseModel):
    behaviorOnRemoteDisconnect: RemoteDisconnectReactionType = RemoteDisconnectReactionType.WAIT_REMOTE_RECONNECT
    behaviorOnRemoteExitSuccess: RemoteExitReactionType = RemoteExitReactionType.CONFIRM_CLOSE
    behaviorOnRemoteExitFailure: RemoteExitReactionType = RemoteExitReactionType.RESTART_WITH_EXPONENTIAL_BACKOFF
    behaviorOnRemotePause: RemotePauseReactionType = RemotePauseReactionType.PAUSE_PANE
    pauseOnInit: bool = False  # to avoid loading too much in once, this can be set to True
    modalResizeAllowed: bool = True
    pauseAllowed: bool = True
    readOnly: bool = False

class PaneParameters(GeometryParameters):
    rememberPausedModals: bool = True
    modalAllowed: bool = True


class RemoteStateType(Enum):
    RUNNING = 1
    PAUSED = 2
    DISCONNECTED = 3
    CLOSED = 4

class RemoteState(BaseModel):
    type: RemoteStateType
    since: datetime

class RunningRemoteState(RemoteState):
    type: Literal[RemoteStateType.RUNNING] = RemoteStateType.RUNNING

class PausedRemoteState(RemoteState):
    type: Literal[RemoteStateType.PAUSED] = RemoteStateType.PAUSED
    lastRunningAt: datetime

class DisconnectedRemoteState(RemoteState):
    type: Literal[RemoteStateType.DISCONNECTED] = RemoteStateType.DISCONNECTED
    lastRunningAt: datetime

class ClosedRemoteState(RemoteState):
    type: Literal[RemoteStateType.CLOSED] = RemoteStateType.CLOSED
    lastRunningAt: datetime
    exitSuccess: bool



# Only 5 types there so that 1 color of the palette match one of these by default
# Complex interact is a big category with forms, lists, lists with interaction, ...
class FramedType(Enum):
    READ_ONLY = 1
    RAW_SESSION = 2
    DOMAIN_SESSION = 3
    WITH_NEW_SESSION = 4
    COMPLEX_INTERACT = 5


class BaseGeometry(BaseModel):
    x: int
    y: int
    w: int
    h: int

class WithId(BaseModel):
    id: int
    name: str | None = None

class BaseGeometryWithId(BaseGeometry, BaseModel):
    pass

class GeometryWithColorAndState(BaseGeometryWithId):
    initialPaletteIndex: int
    color: BaseColor

    windowIndex: int | None

    remoteState: RemoteStateType
    remoteStateIndex: int  # TODO: rename to better match the dynamic part (could be stream, RO data, complex data, ...)
    framedType: FramedType

    isPaused: bool
    isInitialized: bool  # if not initialized and paused, will display a different error message


class ModalInstance(GeometryWithColorAndState):
    zIndex: int
    isEnabled: bool
    paneIndex: int | None

class PaneInstance(GeometryWithColorAndState):
    pauseModals: list[ModalInstance] = []
    isModal: ModalInstance | None = None  # if None, it's not a modal, otherwise zindex1 > zindex2 => window 1 over window 2


class PaneExternalController:

    def select(self):
        pass

    def pause(self):
        pass

    def unpause(self):
        pass

    def reconnect(self):
        pass

    def disconnect(self):
        pass

    def close(self):
        pass

    def make_modal(self, target_geometry: BaseGeometry):
        pass

    def rename(self, new_name: str):
        pass

    def link_to(self, remote_state_index: int):
        pass

    def split_pane(self, split_mode: TwoDirections, split_at: int | None = None):  # None is middle
        pass


class PaneInternalController:

    def switch_palette(self, palette: ColorPalette):  # None is middle
        pass

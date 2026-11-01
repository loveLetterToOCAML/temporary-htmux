from enum import Enum

from pydantic import BaseModel
from ulid import ULID



class WithSessionGroupId(BaseModel):
    sessionGroupId: int | None

class WithSessionId(WithSessionGroupId):
    sessionId: int | None

class WithWindowId(WithSessionId):
    windowId: int | None

class WithPaneId(WithWindowId):
    paneId: int



SessionGroupIdType = ULID

class GeometryIntent(BaseModel):
    pass

class NewSessionGroupIntent(GeometryIntent):
    name: str | None = None
    isDefault: bool = False
    parentSessionGroup: SessionGroupIdType
    persist: bool = True
    timeConstraints: TimeConstraints | None = None
    timeConstraintsAfterLastInteract: TimeConstraints | None = None
    accessControls: AccessControl

class SetDefaultSessionGroupIntent(GeometryIntent):
    name: str


class SwitchToSessionGroup(GeometryIntent):
    pass

class SwitchToParentSessionGroup(SwitchToSessionGroupIntent):
    pass

class SwitchToRootSessionGroup(GeometryIntent):
    pass

class SwitchToChildSessionGroup(GeometryIntent):
    childId: SessionGroupIdType

class SwitchToSiblingSessionGroup(GeometryIntent):
    siblingId: SessionGroupIdType



class NewWindow(GeometryIntent):
    name: str | None = None


class TwoDirections(Enum):
    HORIZONTAL = 1
    VERTICAL = 2

class FourDirections(Enum):
    LEFT = 1
    UP = 2
    RIGHT = 3
    DOWN = 4

class FocusPane(GeometryIntent):
    pass

class FocusNearPane(GeometryIntent):
    direction: FourDirections

class FocusPaneWithId(GeometryIntent):
    paneId: int

class FocusNextPane(GeometryIntent):
    pass

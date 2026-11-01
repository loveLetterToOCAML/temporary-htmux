from pydantic import BaseModel

from interact.observable_session.color import ColorPalette
from interact.observable_session.session import SessionInstance


class SessionGroupParameters(BaseModel):
    initTowerSession: bool = True  # this gives a default session with all info / interaction on current session group
    initContext: int
    keepEmptySession: bool = True
    disconnectPanesOnDetach: bool = True


class SessionGroupNode(BaseModel):
    currentSessionCounter: int   # counter with auto increment

    currentSessionIndex: int
    sessions: dict[int, SessionInstance]

    defaultPaletteForNewSession: ColorPalette
    contextIndexForNewSession: int


class SessionGroupTree:
    root: int
    parent: int
    children: list[int]


class SessionGroupExternalController:

    def new_session_group(self, parent: int | None, from_template_index: int) -> int:
        pass

    def switch_to_session(self, session_index: int):
        pass

    def switch_to_next_session(self):
        pass

    def rename(self, new_name: str):
        pass

    def set_default_palette(self, new_palette: ColorPalette):
        pass

    def set_default_context(self, context_index: int):
        pass

    def enter(self):
        pass

    def detach(self):
        pass

    def reset_to_template(self):
        pass

    def complete_missing_from_template(self):
        pass

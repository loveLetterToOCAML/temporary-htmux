from interact.observable_session.window import WindowInstance
from interact.observable_session.color import ColorPalette
from interact.observable_session.pane import WithId

from pydantic import BaseModel


class SessionParameters(BaseModel):
    copyWindowSourcePalette: bool = False  # keep default palette on window creation
    copyWindowSourceContext: bool = True  # prefer copying current window context on window creation
    copyPaneSourceContext: bool = True  # prefer copying current pane context on window creation


class SessionInstance(WithId):
    currentWindowCounter: int   # counter with auto increment

    currentWindowIndex: int
    windows: dict[int, WindowInstance]

    defaultPaletteForNewWindow: ColorPalette
    contextIndexForNewWindow: int  # ? unsure


class SessionExternalController:

    def new_window(self, name: str | None, from_window_index: int | None, from_pane_index: int | None) -> int:
        pass

    def switch_to_window(self, window_index: int):
        pass

    def switch_to_next_window(self):
        pass

    def rename(self, new_name: str):
        pass

    def set_default_palette(self, new_palette: ColorPalette):
        pass

    def set_default_context(self, context_index: int):
        pass


class SessionInternalController:

    def close_window(self):
        pass

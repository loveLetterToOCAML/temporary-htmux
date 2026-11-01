from interact.observable_session.pane import PaneInstance, ModalInstance, BaseGeometryWithId
from interact.observable_session.geometry_intents import TwoDirections, FourDirections
from interact.observable_session.color import ColorPalette, BaseColor


class WindowInstance(BaseGeometryWithId):
    currentPaneCounter: int   # counter with auto increment
    currentModalCounter: int

    currentPaneIndex: int
    panes: dict[int, PaneInstance]  # dict is to permit easy removal of panes
    floatingModals: dict[int, ModalInstance]

    palette: ColorPalette
    currentColor: BaseColor

    contextIndex: int  # ? unsure


class WindowExternalController:

    def new_pane(self, context_index: int | None = None, palette: ColorPalette | None = None):
        pass

    def new_modal(self, context_index: int | None = None, palette: ColorPalette | None = None):
        pass

    def rename(self, new_name: str):
        pass

    def switch_palette(self, new_palette: ColorPalette):
        pass

    def close(self):
        pass


class WindowInternalController:

    def split_pane(self, pane_id: int, split_mode: TwoDirections, split_at: int | None = None):  # None is middle
        pass

    def select_pane_with_index(self, pane_id: int):
        pass

    def select_next_pane(self):
        pass

    def select_pane_with_direction(self, pane_direction: FourDirections):
        pass

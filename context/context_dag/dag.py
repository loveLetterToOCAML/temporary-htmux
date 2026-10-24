from typing import Type, TypeVar, Callable, Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import Enum


class ContextType(Enum):
    NO_PERSISTENCE = 1   # context var with no persistence, always initialized to its default value (state can be saved though)
    SIMPLE_RO = 2        # simple read only object that will not be saved at the end of the task (cannot be modified)
    BOOLEAN_CACHE = 3    # trivial boolean to hold clear cache that get persisted at its usage end
    SIMPLE_RW_CACHE = 4  # simple cache that get persisted at its usage end
    NAMED_MULTIPLE = 5   # when we enforce indirection and named contexts at current level
    PARTIAL_OBJECT = 6   # when only providing subset of pydantic object, with other attributes being resolved separately
    OBJECT_SUBPART = 7   # when only a sub-object of an upper context var is needed as a view
    STATE_MACHINE = 8    # value that is given to a state machine object and which will be saved on each value change
    INFINITE_CONTROLLER = 9  # construct an infinite controller depending on upper context, and always up
    EFFECT_HANDLER = 10  # construct an effect handler, which are specifically handled at topmost level


T = TypeVar('T')


class ContextVarNode:

    def __init__(self, var: ContextVar[T], context_type: ContextType,
                 T_constructor: Type[T] | Callable[[], T] | Callable[[], Awaitable[T]],
                 parent: ContextVar | None = None):
        self._var = var
        self._parent = parent
        self._context_type = context_type
        self._instance_constructor = T_constructor


class CurrentContextDAG:

    def __init__(self):
        self._dependencies_graph = {}
        self._nodes = {}

    def ancestors_contains(self, from_node: ContextVar[T], search_node: ContextVar):
        cur_dep = self._dependencies_graph.get(from_node)
        while cur_dep:
            if cur_dep == search_node:
                return True
            cur_dep = self._dependencies_graph.get(cur_dep)

    def register_context_var_exn(self, ctx_var: ContextVar[T], context_type: ContextType, T_constructor: Type[T] | Callable[[], T] | Callable[[], Awaitable[T]], parent: ContextVar | None = None):
        if ctx_var == parent:
            raise NodeCannotBeItsOwnParent()
        if ctx_var in self._dependencies_graph:
            raise AlreadyRegisteredContextVar()
        if parent in self._dependencies_graph and ctx_var in self._nodes and self.ancestors_contains(parent, ctx_var):
            raise CycleDetected()
        self._dependencies_graph[ctx_var] = parent
        self._nodes[ctx_var] = ContextVarNode(ctx_var, context_type, T_constructor, parent)
        if parent not in self._nodes:  # pre-reserve parent node
            self._nodes = None

    def register_readonly_model_var_exn(self, ctx_var: ContextVar[T],
                                    T_constructor: Type[T] | Callable[[], T] | Callable[[], Awaitable[T]],
                                    allow_multiple_named: bool = True):
        self.register_context_var_exn(ctx_var, ContextType.SIMPLE_RO, T_constructor)

    def register_readonly_model_var_in_config(self, ctx_var: ContextVar[T],
                                              T_constructor: Type[T] | Callable[[], T] | Callable[[], Awaitable[T]]):
        self.register_readonly_model_var_exn(ctx_var, T_constructor,
                                             allow_multiple_named=context_config.DefaultAllowMultipleNamed)

    def _resolve_var(self, ctx_var: ContextVar[T]):
        pass

    def _resolve_var_dependencies(self, ctx_vars: list[ContextVar]):
        numbered_dependencies = [(ctx_var, i) for i, ctx_var in enumerate(ctx_vars)]


current_context_graph = ContextVar[CurrentContextDAG]('current_context_graph')


@asynccontextmanager
def run_with_variables(ctx_vars: list[ContextVar]):
    pass

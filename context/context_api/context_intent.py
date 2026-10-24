from enum import Enum
from typing import Literal

from pydantic import BaseModel


class NewContextIntentType(Enum):
    FORK_INHERIT = 1
    FORK_NO_INHERIT = 2  # will only take parent and root from above, otherwise will create empty context
    TEMPLATE = 3         # will use existing context template for crafting new context
    TEMPLATE_AND_MERGE = 4  # will use existing context template and merge it with above context
    PARENT_AS_NEW_ROOT = 5
    UNLINK_PARENT = 5   # this even unlinks until parent, so nothing is shared, and there is no way back

class ContextInheritancePolicy(Enum):
    INHERIT_DEFAULT = 1
    NO_INHERITANCE = 2
    UNLINK_ROOT = 3
    UNLINK_PARENT = 4  # this even unlinks until parent, so nothing is shared, and there is no way back


class NewContextIntent(BaseModel):
    name: str
    inheritPolicy: ContextInheritancePolicy
    template: str

class UnlinkContextIntent(BaseModel):
    name: str

# TODO: do we allow context removal?


class SwitchContextIntentType(Enum):
    GO_TO_PARENT = 1
    GO_TO_ROOT = 2
    GO_TO_CHILD = 3
    LINK_EXISTING_CONTEXT = 4

class SwitchContextToIntent(BaseModel):
    contextSwitchType: SwitchContextIntentType
    setAsDefaultContext: bool = False

class SwitchContextToParentIntent(SwitchContextToIntent):
    contextSwitchType: Literal[SwitchContextIntentType.GO_TO_PARENT] = SwitchContextIntentType.GO_TO_PARENT

class SwitchContextToRootIntent(SwitchContextToIntent):
    contextSwitchType: Literal[SwitchContextIntentType.GO_TO_ROOT] = SwitchContextIntentType.GO_TO_ROOT

class SwitchContextToChildIntent(SwitchContextToIntent):
    contextSwitchType: Literal[SwitchContextIntentType.GO_TO_CHILD] = SwitchContextIntentType.GO_TO_CHILD
    childName: str


class SwitchContextBackIntent(BaseModel):
    backToInitial: bool = False
    depthBack: int = 1


class CollectContextsToRootIntent(BaseModel):
    pass

class InvalidParentContextError(Enum):
    DATASOURCE_NOT_REACHABLE = 1
    BAD_ROOT = 2
    NOT_IN_CHILDREN = 3

class ContextNode:
    reference: str
    datasourceReference: T
    status: bool | InvalidParentContextError | None  # None = not resolved, bool : must be true, or InvalidParentContextError

class CollectContextsToRootResponse(BaseModel):
    tree: Tree[ContextNode[T]]
    validContextTree: bool

# ensure all parents have same root until root
# can also check all available children recursively
class CheckContextsToRootIntent(BaseModel):
    pass

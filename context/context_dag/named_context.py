from pydantic import BaseModel

from typing import TypeVar, Generic
from enum import Enum


# TODO: how do we handle real lists? Maybe lists should be declared as base objects
class UsageType(Enum):
    EXACTLY_ONE = 1  # enforce one contextvar being set after object being parsed (staying within the same task)
    ALL = 2          # this will create many tasks, one per (in the limit of the scheduler)
    NONE = 3         # this will create no child task, and required proper addressing later
    MULTIPLE_CHOICE = 4  # this will enforce a choice being made


T = TypeVar('T')

class NamedContexts(BaseModel, Generic[T]):
    perName: dict[str, T]
    defaultValue: str | None = None
    usage: UsageType = UsageType.EXACTLY_ONE

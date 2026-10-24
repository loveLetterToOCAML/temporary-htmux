from enum import Enum
from typing import Literal

from pydantic import BaseModel


class SessionType(Enum):
    RAW_SESSION = 1                 # only immediate stream / custom protocol with no control from around
    RAW_SESSION_WITH_CONTROL = 2    # minimal control from upper layers (history, interact, persistence, switch)
    SESSION_WITH_CONTROL = 3        # ability to schedule, create new sessions, tasks, jobs, services, ...
    FULL_CONTROL_SESSION = 4        # all abilities granted, context creation / modification / switching included
    REMOTE_SESSION = 5              # linked to a computation that happens elsewhere

class ContextReferenceType(Enum):
    SAME_AS_OF_PARENT_SESSION = 1
    SAME_AS_PARENT_OF_PARENT_SESSION = 2
    SAME_AS_ROOT_OF_PARENT_SESSION = 3
    SAME_AS_CHILD_OF_PARENT_SESSION = 4
    OTHER_OF_SAME_DOMAIN = 5
    OTHER_OF_SAME_FOREST = 6
    OTHER = 7

class ContextReference(BaseModel):
    contextReferenceType: ContextReferenceType

class SameContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_OF_PARENT_SESSION

class SameAsParentContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_PARENT_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_PARENT_OF_PARENT_SESSION

class SameAsRootContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_ROOT_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_ROOT_OF_PARENT_SESSION

class SameAsChildContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_CHILD_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_CHILD_OF_PARENT_SESSION
    childContextName: str

class SameContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_OF_PARENT_SESSION

class SameContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_OF_PARENT_SESSION

class SameContextReference(ContextReference):
    contextReferenceType: Literal[ContextReferenceType.SAME_AS_OF_PARENT_SESSION] = ContextReferenceType.SAME_AS_OF_PARENT_SESSION


class Session(BaseModel):
    sessionType: SessionType
    context: ContextReference  # why not ContextIdentifierForCompute?
    computeReference: ComputeReferenceInDomain | None = None   # mostly useful for remote sessions, otherwise these are locally tied to current compute engine

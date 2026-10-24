from pydantic import BaseModel


class ExecutionAtomIntent(BaseModel):
    intent: TYPE
    configName: str
    stateName: str
    runAsIdentityName: str

class ExecutionTimeIntent(BaseModel):
    scheduleFirstAt: datetime
    repeatEvery: timedelta | None = None  # if none, will run only once
    stopAfter: datetime | None = None  # if none, will run every repeatEvery time if not None
    neverScheduleOn: IntervalUnion[datetime] | DynamicTimeConcept  # holidays, night, ...

class ExecutionIntent(BaseModel):
    atomIntent: ExecutionAtomIntent
    timeIntent: ExecutionTimeIntent
    contextualRestrictions: ExecutionContextRestrictions  # not twice the same intent running simultaneously; only if not on battery; or any other contextual condition

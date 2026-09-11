"""Which telemetry a profile gets, decided in one place.

The same rule as `app/memory/open.py`: the local and deployed profiles run the
same `app/`, so a file on a personal machine and a networked database are
configuration rather than a fork. `AGENT_TELEMETRY=0` gives a `Telemetry` with
no store at all, and every call site keeps working because what it hands back
then is a trace that records nothing.

The PostgreSQL import is deferred so a machine without the driver still starts.
"""

from __future__ import annotations

from app.config import AgentSettings
from app.telemetry.trace import Telemetry
from app.trajectories import Trajectories


def open_telemetry(
    settings: AgentSettings | None = None, *, migrate_schema: bool = False
) -> Telemetry:
    settings = settings or AgentSettings()
    # The trajectories ride on the trace because the trace is what every model
    # call already passes through with the run's ids; they are written even
    # when the telemetry store is off, since they are data, not measurement.
    trajectories = Trajectories.at(settings.trajectories)
    if not settings.telemetry:
        return Telemetry(None, trajectories=trajectories)
    if settings.database_url:
        from app.telemetry.postgres import PostgresTelemetry

        return Telemetry(
            PostgresTelemetry(
                settings.database_url,
                settings.database_schema,
                migrate_schema=migrate_schema,
            ),
            trajectories=trajectories,
        )
    from app.telemetry.sqlite import SqliteTelemetry

    return Telemetry(SqliteTelemetry(settings.telemetry_database), trajectories=trajectories)

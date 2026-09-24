from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


VERSION="risk-center-projection@1.0.0"
READY="READY"
WAITING="WAITING"
ERROR="ERROR"


def _section(state:str,data=None,*,reason:str|None=None,source:str|None=None)->dict:
    state=str(state or ERROR).upper()
    if state not in {READY,WAITING,ERROR}:
        raise ValueError(f"invalid risk projection state:{state}")
    if state!=READY and not reason:
        raise ValueError("non-ready risk projection section requires reason")
    return {
        "state":state,
        "reason":reason,
        "source":source,
        "data":deepcopy(data) if data is not None else {},
    }


class RiskCenterProjection:
    """Stable UI read contract for the risk center.

    The browser consumes one projection instead of independently joining
    warning/control domain APIs. Domain endpoints remain available for research
    and diagnostics but are not page composition dependencies.
    """

    version=VERSION

    def __init__(self,engine)->None:
        self.engine=engine

    @staticmethod
    def _read(read_fn,source:str,missing_reason:str)->dict:
        try:
            row=read_fn()
        except Exception as exc:
            return _section(
                ERROR,
                {},
                reason=f"{type(exc).__name__}:{exc}",
                source=source,
            )
        if row is None:
            return _section(
                WAITING,
                {},
                reason=missing_reason,
                source=source,
            )
        return _section(READY,row,source=source)

    def full(self)->dict:
        warning=self._read(
            self.engine.risk_warning_latest,
            "risk_warning.latest",
            "WAITING_FOR_FIRST_RISK_WARNING_SNAPSHOT",
        )
        control=self._read(
            self.engine.risk_control_latest,
            "risk_control.latest",
            "WAITING_FOR_FIRST_RISK_CONTROL_SNAPSHOT",
        )
        sections={
            "warning":warning,
            "control":control,
        }
        errors=[
            f"{name}:{section.get('reason')}"
            for name,section in sections.items()
            if section.get("state")==ERROR
        ]
        warnings=[
            f"{name}:{section.get('reason')}"
            for name,section in sections.items()
            if section.get("state")==WAITING
        ]
        return {
            "contract_version":self.version,
            "projection_scope":"RISK_CENTER",
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "sections":sections,
            "integrity":{
                "passed":not errors,
                "status":"BLOCKED" if errors else ("DEGRADED" if warnings else "READY"),
                "errors":errors,
                "warnings":warnings,
                "frontend_safe":not errors,
                "rule":"RISK_CENTER_UI_CONSUMES_ONE_READ_PROJECTION; NO_BROWSER_DOMAIN_JOIN",
            },
        }

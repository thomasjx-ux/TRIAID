from __future__ import annotations

import os
from datetime import datetime, timezone
from statistics import median
from threading import RLock
from zoneinfo import ZoneInfo


class DecisionScheduler:
    version="decision-scheduler@0.1.0"

    def __init__(self,engine)->None:
        self.engine=engine
        self.store=engine.store
        self.enabled=os.getenv("TRIAID_DECISION_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.state_name="decision_scheduler_state.json"
        self.events_name="decision_events.jsonl"
        self.warmup_transitions=20
        self.state=self.store.load_json(self.state_name,default={}) or {}
        self.state.setdefault("markets",{})
        self._lock=RLock()

    @staticmethod
    def _tz(market_id:str)->ZoneInfo:
        return ZoneInfo("America/New_York" if market_id.upper()=="US" else "Asia/Shanghai")

    def session_date(self,market_id:str)->str:
        return datetime.now(self._tz(market_id)).date().isoformat()

    def _market_state(self,market_id:str)->dict:
        market=market_id.upper()
        day=self.session_date(market)
        raw=self.state["markets"].get(market)
        if not raw or raw.get("session_date")!=day:
            raw={
                "session_date":day,
                "baseline_done":False,
                "baseline_event_id":None,
                "last_decision_source_ts":None,
                "last_close_source_ts":None,
                "close_done":False,
                "close_event_id":None,
                "decision_count":0,
            }
            self.state["markets"][market]=raw
            self._save()
        return raw

    def _save(self)->None:
        self.store.save_json(self.state_name,self.state)

    def _event(self,market_id:str,event_type:str,payload:dict)->dict:
        market=market_id.upper()
        row={
            "event_id":f"{market}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
            "created_at":datetime.now(timezone.utc).isoformat(),
            "session_date":self.session_date(market),
            "market_id":market,
            "event_type":event_type,
            "research_only":True,
            "broker_order_generated":False,
            **payload,
        }
        self.store.append_jsonl(self.events_name,row)
        return row

    def events(self,market_id:str|None=None,limit:int=500)->list[dict]:
        rows=self.store.read_jsonl(self.events_name,limit=max(limit*4,limit))
        if market_id:
            market=market_id.upper()
            rows=[r for r in rows if str(r.get("market_id","")).upper()==market]
        return rows[-limit:]

    @staticmethod
    def _transition_state(row:dict)->tuple[int,str]:
        mean_return=float(row.get("mean_return") or 0.0)
        direction=1 if mean_return>0 else (-1 if mean_return<0 else 0)
        adv=int(row.get("advancers") or 0)
        dec=int(row.get("decliners") or 0)
        breadth="POS" if adv>dec else ("NEG" if dec>adv else "MIXED")
        return direction,breadth

    @staticmethod
    def _score(row:dict)->float:
        mean_abs=max(0.0,float(row.get("mean_abs_return") or 0.0))
        dispersion=max(0.0,float(row.get("cross_sectional_dispersion") or 0.0))
        max_abs=max(0.0,float(row.get("max_abs_return") or 0.0))
        return max(mean_abs,dispersion,0.5*max_abs)

    def assess_transition(self,market_id:str,mode:str,transition:dict)->dict:
        market=market_id.upper();mode=mode.upper()
        score=self._score(transition)
        if score<=0:
            return {
                "trigger":False,
                "reason":"NO_PRICE_CHANGE",
                "score":score,
                "prior_samples":0,
            }

        source_ts=transition.get("source_latest_ts")
        rows=[
            r for r in self.engine.market_transitions(market,mode,250)
            if r.get("source_latest_ts")!=source_ts
        ]
        prior=rows[-200:]
        prior_scores=[self._score(r) for r in prior if self._score(r)>0]

        if len(prior_scores)<self.warmup_transitions:
            return {
                "trigger":True,
                "reason":"WARMUP_HIGH_SENSITIVITY",
                "score":score,
                "prior_samples":len(prior_scores),
                "threshold":0.0,
            }

        center=median(prior_scores)
        mad=median([abs(x-center) for x in prior_scores])
        threshold=center+mad
        previous=prior[-1] if prior else None
        state_changed=bool(
            previous
            and self._transition_state(previous)!=self._transition_state(transition)
        )
        trigger=score>=threshold or state_changed
        return {
            "trigger":trigger,
            "reason":"ROBUST_SALIENCE_OR_STATE_CHANGE" if trigger else "BELOW_ADAPTIVE_SALIENCE",
            "score":score,
            "prior_samples":len(prior_scores),
            "median_score":center,
            "mad":mad,
            "threshold":threshold,
            "state_changed":state_changed,
        }

    def _ensure_baseline(self,market_id:str,phase:str)->dict|None:
        market=market_id.upper()
        state=self._market_state(market)
        if state["baseline_done"]:
            return None

        reference=self.engine.latest_decision_run(market)
        attempted=None
        if reference is None:
            attempted=self.engine.run_live_research(market)
            reference=self.engine.latest_decision_run(market)

        payload={
            "phase":phase,
            "basis":(
                "PRIOR_CLOSE_NO_AUCTION_FEED"
                if market=="CN" and phase=="PREOPEN"
                else "LATEST_AVAILABLE_DECISION"
            ),
            "reference_run_id":reference.run_id if reference else None,
            "attempt_run_id":attempted.run_id if attempted else None,
            "attempt_status":attempted.status if attempted else None,
            "weights_after":(
                dict(reference.triaid_decision.weights_after)
                if reference and reference.triaid_decision
                else {}
            ),
            "decision_available":bool(reference and reference.triaid_decision),
        }
        event_type="PREOPEN_BASELINE" if phase=="PREOPEN" else "OPEN_LATE_BASELINE"
        row=self._event(market,event_type,payload)
        state["baseline_done"]=True
        state["baseline_event_id"]=row["event_id"]
        self._save()
        return row

    def _transition_decision(self,market_id:str,mode:str,transition:dict)->dict|None:
        market=market_id.upper()
        state=self._market_state(market)
        source_ts=transition.get("source_latest_ts")
        try:
            current_ts=int(source_ts)
            last_ts=state.get("last_decision_source_ts")
            if last_ts is not None and current_ts<=int(last_ts):
                return None
        except Exception:
            if source_ts==state.get("last_decision_source_ts"):
                return None

        assessment=self.assess_transition(market,mode,transition)
        if not assessment["trigger"]:
            self._event(market,"TRANSITION_SKIPPED",{
                "phase":"OPEN",
                "mode":mode.upper(),
                "source_latest_ts":source_ts,
                "assessment":assessment,
            })
            return None

        result=self.engine.recompute_transition_research(market,transition,mode)
        row=self._event(market,"TRANSITION_RESEARCH_DECISION",{
            "phase":"OPEN",
            "mode":mode.upper(),
            "source_latest_ts":source_ts,
            "assessment":assessment,
            "decision":result,
        })
        state["last_decision_source_ts"]=source_ts
        state["decision_count"]=int(state.get("decision_count",0))+1
        self._save()
        return row

    def _close(self,market_id:str,snapshot:dict)->dict|None:
        market=market_id.upper()
        state=self._market_state(market)
        if state.get("close_done"):
            return None
        source_ts=snapshot.get("source_latest_ts")
        if source_ts==state.get("last_close_source_ts"):
            return None

        run=self.engine.run_live_research(market)
        event_type="CLOSE_FINAL" if run.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"} else "CLOSE_WAITING_FOR_NEW_DAILY_DATA"
        row=self._event(market,event_type,{
            "phase":"POSTCLOSE",
            "source_latest_ts":source_ts,
            "run_id":run.run_id,
            "run_status":run.status,
            "snapshot_id":run.market.snapshot_id,
        })
        state["last_close_source_ts"]=source_ts
        if run.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}:
            state["close_done"]=True
            state["close_event_id"]=row["event_id"]
        self._save()
        return row

    def after_refresh(
        self,
        market_id:str,
        mode:str,
        snapshot:dict,
        observed:dict,
    )->dict:
        market=market_id.upper();mode=mode.upper()
        if not self.enabled:
            return {"enabled":False,"action":"DISABLED"}

        phase=str(snapshot.get("session_phase") or "").upper()
        with self._lock:
            if phase in {"PREOPEN","OPEN"}:
                baseline=self._ensure_baseline(market,phase)
                if baseline is not None and phase=="PREOPEN":
                    return {"enabled":True,"action":"BASELINE_RECORDED","event":baseline}

            transition=observed.get("transition") if isinstance(observed,dict) else None
            if phase=="OPEN" and transition:
                event=self._transition_decision(market,mode,transition)
                return {
                    "enabled":True,
                    "action":"TRANSITION_EVALUATED",
                    "event":event,
                }

            if phase=="POSTCLOSE" and mode=="DAILY":
                event=self._close(market,snapshot)
                return {
                    "enabled":True,
                    "action":"CLOSE_EVALUATED",
                    "event":event,
                }

        return {"enabled":True,"action":"NO_DECISION_EVENT"}

    def status(self)->dict:
        return {
            "version":self.version,
            "enabled":self.enabled,
            "discipline":"PREOPEN_BASELINE_THEN_ADAPTIVE_TRANSITION_RESEARCH_THEN_CLOSE_FINAL_NO_BROKER_EXECUTION",
            "warmup_transitions":self.warmup_transitions,
            "markets":{
                market:self._market_state(market)
                for market in ("US","CN")
            },
            "event_count":len(self.events(limit=10000)),
            "broker_execution_enabled":False,
        }

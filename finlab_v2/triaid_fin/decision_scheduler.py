from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from statistics import median
from threading import RLock
from zoneinfo import ZoneInfo

from .trading_calendar import trading_day_info
from .market_registry import MARKET_REGISTRY, market_ids, normalize_market_id
from .runtime_ports import RuntimeServices

def _env_int(name:str,default:int)->int:
    raw=(os.getenv(name) or str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return int(default)

def _env_float(name:str,default:float)->float:
    raw=(os.getenv(name) or str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return float(default)



class DecisionScheduler:
    version="decision-scheduler@0.2.5"

    def __init__(self,services)->None:
        self.services=(
            services
            if isinstance(services,RuntimeServices)
            else RuntimeServices(services)
        )
        self.store=self.services.journal
        self.enabled=os.getenv("TRIAID_DECISION_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.state_name="decision_scheduler_state.json"
        self.events_name="decision_events.jsonl"
        self.warmup_transitions=max(5,_env_int("TRIAID_DECISION_WARMUP_TRANSITIONS",20))
        self.mad_multiplier=max(1.0,_env_float("TRIAID_DECISION_MAD_MULTIPLIER",2.0))
        self.state_confirmations=max(2,_env_int("TRIAID_DECISION_STATE_CONFIRMATIONS",3))
        self.min_recompute_seconds={
            "REALTIME":max(0,_env_int("TRIAID_DECISION_MIN_REALTIME_SECONDS",300)),
            "INTRADAY":max(0,_env_int("TRIAID_DECISION_MIN_INTRADAY_SECONDS",600)),
        }
        self.allocation_action_l1_threshold=max(
            0.0,_env_float("TRIAID_ALLOCATION_ACTION_L1_THRESHOLD",0.05)
        )
        self.close_settle_seconds=max(
            0,_env_int("TRIAID_CLOSE_SETTLE_SECONDS",300)
        )
        self.state=self.store.load_json(self.state_name,default={}) or {}
        self.state.setdefault("markets",{})
        self._lock=RLock()

    @staticmethod
    def _tz(market_id:str)->ZoneInfo:
        market=normalize_market_id(market_id)
        return ZoneInfo(MARKET_REGISTRY.get(market).timezone)

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
                "baseline_fresh":False,
                "baseline_expected_as_of":None,
                "baseline_reference_as_of":None,
                "baseline_refresh_attempt_epoch":0,
                "baseline_refresh_attempt_count":0,
                "last_decision_source_ts":None,
                "last_close_source_ts":None,
                "last_close_signature":None,
                "close_wait_signature":None,
                "close_done":False,
                "close_event_id":None,
                "decision_count":0,
                "allocation_action_count":0,
            }
            self.state["markets"][market]=raw
        else:
            defaults={
                "baseline_done":False,
                "baseline_event_id":None,
                "baseline_fresh":False,
                "baseline_expected_as_of":None,
                "baseline_reference_as_of":None,
                "baseline_refresh_attempt_epoch":0,
                "baseline_refresh_attempt_count":0,
                "last_decision_source_ts":None,
                "last_close_source_ts":None,
                "last_close_signature":None,
                "close_wait_signature":None,
                "close_done":False,
                "close_event_id":None,
                "decision_count":0,
                "allocation_action_count":0,
            }
            changed=False
            for key,value in defaults.items():
                if key not in raw:
                    raw[key]=value
                    changed=True
            # Missing legacy fields are upgraded in memory on read. Mutation
            # paths persist the full state when they actually create an event
            # or decision; status/read endpoints must never write storage.
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
                "urgent":False,
                "reason":"NO_PRICE_CHANGE",
                "score":score,
                "prior_samples":0,
            }

        source_ts=transition.get("source_latest_ts")
        rows=[
            r for r in self.services.market_transitions(market,mode,250)
            if r.get("source_latest_ts")!=source_ts
        ]
        prior=rows[-200:]
        prior_scores=[self._score(r) for r in prior if self._score(r)>0]

        current_state=self._transition_state(transition)
        confirmations=self.state_confirmations
        recent_states=[
            self._transition_state(r)
            for r in prior[-(confirmations-1):]
        ] if confirmations>1 else []
        persistent_current=bool(
            len(recent_states)==confirmations-1
            and all(s==current_state for s in recent_states)
        )
        previous_anchor=(
            self._transition_state(prior[-confirmations])
            if len(prior)>=confirmations
            else None
        )
        confirmed_state_change=bool(
            persistent_current
            and previous_anchor is not None
            and previous_anchor!=current_state
        )

        if len(prior_scores)<self.warmup_transitions:
            return {
                "trigger":True,
                "urgent":False,
                "reason":"WARMUP_CALIBRATION",
                "score":score,
                "prior_samples":len(prior_scores),
                "threshold":None,
                "state_confirmations":confirmations,
                "confirmed_state_change":confirmed_state_change,
            }

        center=median(prior_scores)
        mad=median([abs(x-center) for x in prior_scores])
        threshold=center+self.mad_multiplier*mad
        urgent_threshold=center+4.0*mad
        salient=score>=threshold
        urgent=score>=urgent_threshold and score>center
        trigger=salient or confirmed_state_change
        return {
            "trigger":trigger,
            "urgent":urgent,
            "reason":(
                "ROBUST_SALIENCE"
                if salient
                else (
                    "CONFIRMED_STATE_CHANGE"
                    if confirmed_state_change
                    else "BELOW_ADAPTIVE_SALIENCE"
                )
            ),
            "score":score,
            "prior_samples":len(prior_scores),
            "median_score":center,
            "mad":mad,
            "mad_multiplier":self.mad_multiplier,
            "threshold":threshold,
            "urgent_threshold":urgent_threshold,
            "state_confirmations":confirmations,
            "confirmed_state_change":confirmed_state_change,
            "transition_state":list(current_state),
        }

    def _previous_trading_day(self,market_id:str)->str|None:
        market=market_id.upper()
        today=datetime.now(self._tz(market)).date()
        for offset in range(1,15):
            candidate=today-timedelta(days=offset)
            info=trading_day_info(market,candidate)
            if not info.get("calendar_known"):
                return None
            if info.get("is_trading_day"):
                return candidate.isoformat()
        return None

    def _ensure_baseline(self,market_id:str,phase:str)->dict|None:
        market=market_id.upper()
        state=self._market_state(market)
        expected_as_of=self._previous_trading_day(market)
        reference=self.services.latest_decision_run(market)
        reference_as_of=str(reference.market.as_of) if reference is not None else None
        fresh=bool(
            expected_as_of
            and reference_as_of
            and reference_as_of>=expected_as_of
            and reference is not None
            and reference.triaid_decision is not None
        )
        previous_done=bool(state.get("baseline_done"))
        previous_reference_as_of=state.get("baseline_reference_as_of")
        attempted=None

        if not fresh:
            now_epoch=int(datetime.now(timezone.utc).timestamp())
            last_attempt=int(state.get("baseline_refresh_attempt_epoch") or 0)
            if now_epoch-last_attempt>=300:
                state["baseline_refresh_attempt_epoch"]=now_epoch
                state["baseline_refresh_attempt_count"]=int(
                    state.get("baseline_refresh_attempt_count") or 0
                )+1
                attempted=self.services.run_live_research(market)
                reference=self.services.latest_decision_run(market)
                reference_as_of=str(reference.market.as_of) if reference is not None else None
                fresh=bool(
                    expected_as_of
                    and reference_as_of
                    and reference_as_of>=expected_as_of
                    and reference is not None
                    and reference.triaid_decision is not None
                )
            else:
                state["baseline_done"]=False
                state["baseline_fresh"]=False
                state["baseline_expected_as_of"]=expected_as_of
                state["baseline_reference_as_of"]=reference_as_of
                self._save()
                return None

        if previous_done and fresh and previous_reference_as_of==reference_as_of:
            state["baseline_fresh"]=True
            state["baseline_expected_as_of"]=expected_as_of
            state["baseline_reference_as_of"]=reference_as_of
            return None

        payload={
            "phase":phase,
            "basis":(
                "PRIOR_CLOSE_NO_AUCTION_FEED"
                if market=="CN" and phase=="PREOPEN"
                else "LATEST_COMPLETE_DAILY_DECISION"
            ),
            "expected_reference_as_of":expected_as_of,
            "reference_as_of":reference_as_of,
            "reference_fresh":fresh,
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
        if phase=="PREOPEN":
            if not fresh:
                event_type="PREOPEN_BASELINE_STALE"
            elif previous_done:
                event_type="PREOPEN_BASELINE_REFRESHED"
            else:
                event_type="PREOPEN_BASELINE"
        else:
            if not fresh:
                event_type="OPEN_LATE_BASELINE_STALE"
            elif previous_done:
                event_type="OPEN_LATE_BASELINE_REFRESHED"
            else:
                event_type="OPEN_LATE_BASELINE"
        row=self._event(market,event_type,payload)
        state["baseline_done"]=bool(fresh)
        state["baseline_fresh"]=bool(fresh)
        state["baseline_expected_as_of"]=expected_as_of
        state["baseline_reference_as_of"]=reference_as_of
        state["baseline_event_id"]=row["event_id"]
        self._save()
        return row

    def _transition_decision(self,market_id:str,mode:str,transition:dict)->dict|None:
        market=market_id.upper();mode=mode.upper()
        state=self._market_state(market)
        source_ts=transition.get("source_latest_ts")
        current_ts=None
        last_ts=None
        try:
            current_ts=int(source_ts)
            last_raw=state.get("last_decision_source_ts")
            last_ts=int(last_raw) if last_raw is not None else None
            if last_ts is not None and current_ts<=last_ts:
                return None
        except Exception:
            if source_ts==state.get("last_decision_source_ts"):
                return None

        assessment=self.assess_transition(market,mode,transition)
        min_interval=int(self.min_recompute_seconds.get(mode,0))
        if (
            assessment.get("trigger")
            and not assessment.get("urgent")
            and current_ts is not None
            and last_ts is not None
            and current_ts-last_ts<min_interval
        ):
            assessment={
                **assessment,
                "trigger":False,
                "reason":"MIN_RECOMPUTE_INTERVAL",
                "elapsed_since_last_decision_seconds":current_ts-last_ts,
                "min_recompute_seconds":min_interval,
            }

        if not assessment["trigger"]:
            self._event(market,"TRANSITION_SKIPPED",{
                "phase":"OPEN",
                "mode":mode,
                "source_latest_ts":source_ts,
                "assessment":assessment,
            })
            return None

        result=self.services.recompute_transition_research(market,transition,mode)
        l1=max(0.0,float(result.get("weight_change_l1_vs_reference") or 0.0))
        raw_allocation_candidate=bool(l1>=self.allocation_action_l1_threshold)
        transition_regime=str(result.get("transition_regime") or "")
        risk_increase_candidate=bool(
            raw_allocation_candidate and transition_regime=="intraday_risk_on"
        )
        persistence_gate_pass=bool(
            not risk_increase_candidate
            or assessment.get("confirmed_state_change") is True
        )
        allocation_change_recommended=bool(
            raw_allocation_candidate and persistence_gate_pass
        )
        result={
            **result,
            "decision_layer":(
                "ALLOCATION_ACTION_CANDIDATE"
                if allocation_change_recommended
                else (
                    "STATE_ONLY_RISK_INCREASE_AWAITING_CONFIRMATION"
                    if risk_increase_candidate and not persistence_gate_pass
                    else "STATE_ONLY_RECOMPUTE"
                )
            ),
            "allocation_change_recommended":allocation_change_recommended,
            "raw_allocation_candidate":raw_allocation_candidate,
            "risk_increase_candidate":risk_increase_candidate,
            "persistence_gate_pass":persistence_gate_pass,
            "persistence_gate_rule":"INTRADAY_RISK_INCREASE_REQUIRES_CONFIRMED_STATE_CHANGE",
            "allocation_action_l1_threshold":self.allocation_action_l1_threshold,
            "risk_increase_persistence_gate":"INTRADAY_RISK_INCREASE_REQUIRES_CONFIRMED_STATE_CHANGE",
            "close_settle_seconds":self.close_settle_seconds,
            "broker_order_generated":False,
        }
        row=self._event(market,"TRANSITION_RESEARCH_DECISION",{
            "phase":"OPEN",
            "mode":mode,
            "source_latest_ts":source_ts,
            "assessment":assessment,
            "decision":result,
        })
        state["last_decision_source_ts"]=source_ts
        state["decision_count"]=int(state.get("decision_count",0))+1
        if allocation_change_recommended:
            state["allocation_action_count"]=int(state.get("allocation_action_count",0))+1
        self._save()
        return row

    def _postclose_settled(self,market_id:str)->bool:
        market=normalize_market_id(market_id)
        now=datetime.now(self._tz(market))
        info=trading_day_info(market,now)
        if info.get("early_close") and info.get("early_close_time"):
            close_text=str(info["early_close_time"])
        else:
            schedule=MARKET_REGISTRY.get(market).session_schedule or {}
            open_windows=list(schedule.get("OPEN") or ())
            if not open_windows:
                return False
            close_text=str(open_windows[-1][1])
        close_hour,close_minute=(int(close_text[:2]),int(close_text[3:5]))
        close_seconds=close_hour*3600+close_minute*60
        now_seconds=now.hour*3600+now.minute*60+now.second
        return now_seconds>=close_seconds+self.close_settle_seconds

    def _close(self,market_id:str,snapshot:dict,observed:dict)->dict|None:
        market=market_id.upper()
        state=self._market_state(market)
        if state.get("close_done"):
            return None

        source_ts=snapshot.get("source_latest_ts")
        signature=self.services.snapshot_signature(snapshot)
        recorded=bool((observed or {}).get("recorded"))
        settled=self._postclose_settled(market)
        if not settled:
            if state.get("close_wait_signature")==signature:
                return None
            row=self._event(market,"CLOSE_WAITING_FOR_NEW_DAILY_DATA",{
                "phase":"POSTCLOSE",
                "source_latest_ts":source_ts,
                "snapshot_signature":signature,
                "observation_reason":(observed or {}).get("reason"),
                "close_settled":False,
                "settle_seconds":self.close_settle_seconds,
            })
            state["last_close_source_ts"]=source_ts
            state["close_wait_signature"]=signature
            self._save()
            return row

        run=self.services.run_live_research(market)
        close_reference_run_id=None
        final_complete=run.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
        if (
            not final_complete
            and run.status=="NO_NEW_DATA"
            and ":FINAL:" in str(run.market.snapshot_id or "")
            and getattr(run,"previous_run_id",None)
        ):
            try:
                reference=self.services.get_run(run.previous_run_id)
            except Exception:
                reference=None
            final_complete=bool(
                reference is not None
                and reference.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
                and reference.market.snapshot_id==run.market.snapshot_id
            )
            if final_complete:
                close_reference_run_id=reference.run_id

        event_type="CLOSE_FINAL" if final_complete else "CLOSE_WAITING_FOR_NEW_DAILY_DATA"
        row=self._event(market,event_type,{
            "phase":"POSTCLOSE",
            "source_latest_ts":source_ts,
            "snapshot_signature":signature,
            "observation_content_revision":bool((observed or {}).get("content_revision")),
            "close_settled":settled,
            "recovered_from_duplicate_content":bool(not recorded and settled),
            "run_id":run.run_id,
            "run_status":run.status,
            "snapshot_id":run.market.snapshot_id,
            "close_reference_run_id":close_reference_run_id,
            "close_completion_basis":(
                "EXISTING_COMPLETE_FINAL_SNAPSHOT"
                if close_reference_run_id
                else ("NEW_COMPLETE_FINAL_SNAPSHOT" if final_complete else None)
            ),
        })
        state["last_close_source_ts"]=source_ts
        state["last_close_signature"]=signature
        state["close_wait_signature"]=None if event_type=="CLOSE_FINAL" else signature
        if event_type=="CLOSE_FINAL":
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
                    event_type=str(baseline.get("event_type") or "")
                    action=(
                        "BASELINE_STALE"
                        if event_type.endswith("_STALE")
                        else (
                            "BASELINE_REFRESHED"
                            if event_type.endswith("_REFRESHED")
                            else "BASELINE_RECORDED"
                        )
                    )
                    return {"enabled":True,"action":action,"event":baseline}

            transition=observed.get("transition") if isinstance(observed,dict) else None
            if phase=="OPEN" and transition:
                event=self._transition_decision(market,mode,transition)
                return {
                    "enabled":True,
                    "action":"TRANSITION_EVALUATED",
                    "event":event,
                }

            if phase=="POSTCLOSE" and mode=="DAILY":
                event=self._close(market,snapshot,observed)
                return {
                    "enabled":True,
                    "action":"CLOSE_EVALUATED" if event is not None else "CLOSE_ALREADY_DONE",
                    "event":event,
                }

        return {"enabled":True,"action":"NO_DECISION_EVENT"}

    def status(self)->dict:
        return {
            "version":self.version,
            "enabled":self.enabled,
            "discipline":"OBSERVE_HIGH_FREQUENCY_RECOMPUTE_WITH_HYSTERESIS_ONLY_RECOMMEND_ALLOCATION_ABOVE_THRESHOLD_CLOSE_ON_NEW_FINAL_DAILY_CONTENT_NO_BROKER_EXECUTION",
            "warmup_transitions":self.warmup_transitions,
            "mad_multiplier":self.mad_multiplier,
            "state_confirmations":self.state_confirmations,
            "min_recompute_seconds":dict(self.min_recompute_seconds),
            "allocation_action_l1_threshold":self.allocation_action_l1_threshold,
            "markets":{
                market:self._market_state(market)
                for market in market_ids()
            },
            "event_count":len(self.events(limit=10000)),
            "broker_execution_enabled":False,
        }

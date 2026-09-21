from __future__ import annotations

import math

from dataclasses import dataclass,asdict
from datetime import datetime,timezone


@dataclass
class FrequencyEvidence:
    evaluated_samples:int=0
    incremental_net_return:float|None=None
    incremental_information_gain:float|None=None
    incremental_cost:float|None=None
    confidence:float|None=None
    updated_at:str|None=None


class FrequencyPolicy:
    version="frequency-policy@0.2.0"

    LADDERS={
        "REALTIME":[60,120,300,600,900,1800],
        "INTRADAY":[300,600,900,1800,3600],
        "PREOPEN":[300,600,900,1800],
        "DAILY":[600,1800,3600],
    }

    def __init__(self,store)->None:
        self.store=store
        self.filename="frequency_policy.json"
        self.min_samples=20
        self.min_confidence=0.60
        self.min_incremental_net_return=0.0
        self.min_information_gain=0.0
        self.state=self.store.load_json(self.filename,default={}) or {}
        self.state.setdefault("levels",{})
        self.state.setdefault("evidence",{})
        self.state.setdefault("manual_locks",{})

    def _key(self,market_id:str,mode:str)->str:
        return f"{market_id.upper()}:{mode.upper()}"

    def _mode(self,mode:str)->str:
        key=mode.upper()
        if key not in self.LADDERS:
            raise ValueError(f"unsupported_frequency_mode:{mode}")
        return key

    def level(self,market_id:str,mode:str)->int:
        mode=self._mode(mode);key=self._key(market_id,mode)
        raw=int(self.state["levels"].get(key,0) or 0)
        return max(0,min(raw,len(self.LADDERS[mode])-1))

    def interval(self,market_id:str,mode:str)->int:
        mode=self._mode(mode)
        return self.LADDERS[mode][self.level(market_id,mode)]

    def set_level(self,market_id:str,mode:str,level:int,*,lock:bool=False,reason:str="manual")->dict:
        mode=self._mode(mode);key=self._key(market_id,mode)
        level=max(0,min(int(level),len(self.LADDERS[mode])-1))
        self.state["levels"][key]=level
        if lock:
            self.state["manual_locks"][key]={
                "locked":True,
                "reason":reason,
                "at":datetime.now(timezone.utc).isoformat(),
            }
        elif key in self.state["manual_locks"]:
            self.state["manual_locks"].pop(key,None)
        self._save()
        return self.status_one(market_id,mode)

    def set_interval(self,market_id:str,mode:str,seconds:int,*,lock:bool=False,reason:str="manual")->dict:
        mode=self._mode(mode)
        ladder=self.LADDERS[mode]
        level=min(range(len(ladder)),key=lambda i:abs(ladder[i]-int(seconds)))
        return self.set_level(market_id,mode,level,lock=lock,reason=reason)

    def unlock(self,market_id:str,mode:str)->dict:
        key=self._key(market_id,mode)
        self.state["manual_locks"].pop(key,None)
        self._save()
        return self.status_one(market_id,mode)

    def record_evidence(
        self,
        market_id:str,
        mode:str,
        *,
        evaluated_samples:int,
        incremental_net_return:float|None,
        incremental_information_gain:float|None=None,
        incremental_cost:float|None=None,
        confidence:float|None=None,
    )->dict:
        mode=self._mode(mode);key=self._key(market_id,mode)
        for label,value in {
            "incremental_net_return":incremental_net_return,
            "incremental_information_gain":incremental_information_gain,
            "incremental_cost":incremental_cost,
            "confidence":confidence,
        }.items():
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{label} must be finite when provided")
        evidence=FrequencyEvidence(
            evaluated_samples=max(0,int(evaluated_samples)),
            incremental_net_return=incremental_net_return,
            incremental_information_gain=incremental_information_gain,
            incremental_cost=incremental_cost,
            confidence=confidence,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self.state["evidence"][key]=asdict(evidence)
        action="HOLD_HIGHER_FREQUENCY"

        locked=bool((self.state["manual_locks"].get(key) or {}).get("locked"))
        enough_samples=evidence.evaluated_samples>=self.min_samples
        enough_confidence=(evidence.confidence or 0.0)>=self.min_confidence
        observed=[]
        if evidence.incremental_net_return is not None:
            observed.append(float(evidence.incremental_net_return)>self.min_incremental_net_return)
        if evidence.incremental_information_gain is not None:
            observed.append(float(evidence.incremental_information_gain)>self.min_information_gain)

        current=self.level(market_id,mode)
        if locked:
            action="MANUAL_LOCK_HOLD"
        elif not enough_samples or not enough_confidence:
            action="HOLD_INSUFFICIENT_EVIDENCE"
        elif not observed:
            action="HOLD_MISSING_VALUE_EVIDENCE"
        elif not any(observed):
            if current<len(self.LADDERS[mode])-1:
                self.state["levels"][key]=current+1
                action="STEP_DOWN_ONE_LEVEL"
            else:
                action="ALREADY_AT_LOWEST_FREQUENCY"
        elif any(observed):
            if current>0:
                self.state["levels"][key]=current-1
                action="STEP_UP_ONE_LEVEL"
            else:
                action="ALREADY_AT_HIGHEST_FREQUENCY"
        else:
            action="HOLD_CURRENT_FREQUENCY"

        self._save()
        result=self.status_one(market_id,mode)
        result["evaluation_action"]=action
        return result

    def status_one(self,market_id:str,mode:str)->dict:
        mode=self._mode(mode);key=self._key(market_id,mode)
        level=self.level(market_id,mode)
        return {
            "market_id":market_id.upper(),
            "mode":mode,
            "policy":"HIGH_TO_LOW",
            "level":level,
            "interval_seconds":self.LADDERS[mode][level],
            "ladder_seconds":list(self.LADDERS[mode]),
            "at_highest_frequency":level==0,
            "at_lowest_frequency":level==len(self.LADDERS[mode])-1,
            "manual_lock":self.state["manual_locks"].get(key),
            "evidence":self.state["evidence"].get(key),
            "downgrade_rule":{
                "min_samples":self.min_samples,
                "min_confidence":self.min_confidence,
                "incremental_net_return_lte":self.min_incremental_net_return,
                "incremental_information_gain_lte":self.min_information_gain,
                "step":"ONE_LEVEL_AT_A_TIME",
            },
        }

    def status(self)->dict:
        return {
            "version":self.version,
            "principle":"START_HIGHEST_STEP_DOWN_ONLY_ON_LOW_MARGINAL_VALUE_AND_ALLOW_STEP_UP_WHEN_HIGHER_FREQUENCY_VALUE_RETURNS",
            "markets":{
                market:{
                    mode:self.status_one(market,mode)
                    for mode in self.LADDERS
                }
                for market in ("US","CN")
            },
        }

    def _save(self)->None:
        self.store.save_json(self.filename,self.state)

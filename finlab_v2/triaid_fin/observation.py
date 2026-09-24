from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
from statistics import mean, pstdev

from .store import RunStore
from .market_registry import MARKET_REGISTRY, normalize_market_id


class MarketObservationStore:
    version="market-observation@0.5.1"

    def __init__(self,store:RunStore)->None:
        self.store=store
        self.filename="market_observations.jsonl"
        self.transition_filename="market_transitions.jsonl"
        self.index_name="market_observation_index.json"
        self.watermark_name="market_observation_watermarks.json"
        self.index=store.load_json(self.index_name,default={}) or {}
        self.watermarks=store.load_json(self.watermark_name,default={}) or {}
        if not isinstance(self.watermarks,dict):
            self.watermarks={}

    @staticmethod
    def _freshness_value(market:str,mode:str,source_latest_ts)->int|None:
        try:
            stamp=int(source_latest_ts)
        except Exception:
            return None
        if str(mode).upper()=="DAILY":
            key=normalize_market_id(market)
            tz=ZoneInfo(MARKET_REGISTRY.get(key).timezone)
            return datetime.fromtimestamp(stamp,tz).date().toordinal()
        return stamp

    @staticmethod
    def snapshot_signature(snapshot:dict)->str:
        payload={
            "market_id":str(snapshot.get("market_id") or "").upper(),
            "mode":str(snapshot.get("mode") or "").upper(),
            "provider":snapshot.get("provider"),
            "source_latest_ts":snapshot.get("source_latest_ts"),
            "interval":snapshot.get("interval"),
            "points":snapshot.get("points"),
            "latest":snapshot.get("latest") or {},
        }
        raw=json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def record(self,snapshot:dict)->dict:
        market=str(snapshot.get("market_id") or "").upper()
        mode=str(snapshot.get("mode") or "").upper()
        source_latest_ts=snapshot.get("source_latest_ts")
        provider=snapshot.get("provider")
        if not market or not mode or source_latest_ts is None:
            return {"recorded":False,"reason":"INVALID_SNAPSHOT"}

        key=f"{market}:{mode}"
        signature=self.snapshot_signature(snapshot)
        try:
            current_ts=int(source_latest_ts)
        except Exception:
            current_ts=None
        current_freshness=self._freshness_value(market,mode,source_latest_ts)
        persisted_watermark=self.watermarks.get(key)
        try:
            persisted_watermark=int(persisted_watermark) if persisted_watermark is not None else None
        except Exception:
            persisted_watermark=None
        persisted_freshness=self._freshness_value(market,mode,persisted_watermark)
        if (
            current_freshness is not None
            and persisted_freshness is not None
            and current_freshness<persisted_freshness
        ):
            return {
                "recorded":False,
                "reason":"STALE_SOURCE_TIMESTAMP",
                "market_id":market,
                "mode":mode,
                "source_latest_ts":source_latest_ts,
                "max_source_latest_ts":persisted_watermark,
                "provider":provider,
            }

        previous=None
        max_source_latest_ts=None
        max_freshness=None
        for candidate in reversed(self.store.read_jsonl(self.filename,limit=500)):
            if (
                str(candidate.get("market_id","")).upper()==market
                and str(candidate.get("mode","")).upper()==mode
            ):
                if previous is None:
                    previous=candidate
                candidate_ts=candidate.get("source_latest_ts")
                candidate_freshness=self._freshness_value(market,mode,candidate_ts)
                if candidate_freshness is not None and (max_freshness is None or candidate_freshness>max_freshness):
                    max_freshness=candidate_freshness
                    try:
                        max_source_latest_ts=int(candidate_ts)
                    except Exception:
                        max_source_latest_ts=candidate_ts

        if (
            current_freshness is not None
            and max_freshness is not None
            and current_freshness<max_freshness
        ):
            return {
                "recorded":False,
                "reason":"STALE_SOURCE_TIMESTAMP",
                "market_id":market,
                "mode":mode,
                "source_latest_ts":source_latest_ts,
                "max_source_latest_ts":max_source_latest_ts,
                "provider":provider,
            }

        previous_signature=None
        if previous is not None:
            previous_signature=previous.get("content_signature") or self.snapshot_signature(previous)
        if self.index.get(key)==signature or previous_signature==signature:
            if self.index.get(key)!=signature:
                self.index[key]=signature
                self.store.save_json(self.index_name,self.index)
            if (
                current_ts is not None
                and (persisted_watermark is None or current_ts>persisted_watermark)
            ):
                self.watermarks[key]=current_ts
                self.store.save_json(self.watermark_name,self.watermarks)
            return {
                "recorded":False,
                "reason":"DUPLICATE_SNAPSHOT_CONTENT",
                "dedupe_basis":"SOURCE_TIMESTAMP_AND_CONTENT",
                "market_id":market,
                "mode":mode,
                "source_latest_ts":source_latest_ts,
                "content_signature":signature,
            }

        provider_changed=bool(
            previous
            and previous.get("provider")
            and previous.get("provider")!=provider
        )
        previous_ts=None
        if previous is not None:
            try:
                previous_ts=int(previous.get("source_latest_ts"))
            except Exception:
                previous_ts=None
        content_revision=bool(
            previous is not None
            and current_ts is not None
            and previous_ts==current_ts
            and previous_signature!=signature
        )

        row={
            "observed_at":datetime.now(timezone.utc).isoformat(),
            "market_id":market,
            "mode":mode,
            "session_phase":snapshot.get("session_phase"),
            "provider":provider,
            "quality":snapshot.get("quality"),
            "execution_grade":bool(snapshot.get("execution_grade",False)),
            "source_latest_ts":source_latest_ts,
            "interval":snapshot.get("interval"),
            "points":snapshot.get("points"),
            "symbols":snapshot.get("symbols") or [],
            "latest":snapshot.get("latest") or {},
            "content_signature":signature,
            "content_revision":content_revision,
            "provider_boundary":provider_changed,
            "previous_provider":previous.get("provider") if provider_changed and previous else None,
        }
        self.store.append_jsonl(self.filename,row)
        transition=self._transition(previous,row) if previous and not content_revision else None
        if transition is not None:
            self.store.append_jsonl(self.transition_filename,transition)
        self.index[key]=signature
        self.store.save_json(self.index_name,self.index)
        if current_ts is not None:
            self.watermarks[key]=max(
                current_ts,
                int(self.watermarks.get(key,current_ts) or current_ts),
            )
            self.store.save_json(self.watermark_name,self.watermarks)
        return {
            "recorded":True,
            "observation":row,
            "transition":transition,
            "content_revision":content_revision,
        }

    def _transition(self,previous:dict,current:dict)->dict|None:
        if previous.get("provider")!=current.get("provider"):
            return None
        prev_latest=previous.get("latest") or {}
        curr_latest=current.get("latest") or {}
        returns={}
        for symbol in sorted(set(prev_latest)&set(curr_latest)):
            try:
                p0=float((prev_latest[symbol] or {}).get("close"))
                p1=float((curr_latest[symbol] or {}).get("close"))
            except (TypeError,ValueError):
                continue
            if p0<=0:
                continue
            returns[symbol]=p1/p0-1.0
        if not returns:
            return None

        vals=list(returns.values())
        advancers=sum(1 for x in vals if x>0)
        decliners=sum(1 for x in vals if x<0)
        unchanged=len(vals)-advancers-decliners
        try:
            elapsed=int(current["source_latest_ts"])-int(previous["source_latest_ts"])
        except Exception:
            elapsed=None
        return {
            "derived_at":datetime.now(timezone.utc).isoformat(),
            "market_id":current.get("market_id"),
            "mode":current.get("mode"),
            "provider":current.get("provider"),
            "quality":current.get("quality"),
            "execution_grade":False,
            "previous_source_latest_ts":previous.get("source_latest_ts"),
            "source_latest_ts":current.get("source_latest_ts"),
            "source_elapsed_seconds":elapsed,
            "symbol_returns":returns,
            "mean_return":mean(vals),
            "mean_abs_return":mean(abs(x) for x in vals),
            "max_abs_return":max(abs(x) for x in vals),
            "cross_sectional_dispersion":pstdev(vals) if len(vals)>1 else 0.0,
            "advancers":advancers,
            "decliners":decliners,
            "unchanged":unchanged,
            "research_only":True,
            "action_generated":False,
        }

    def transitions(
        self,
        market_id:str|None=None,
        mode:str|None=None,
        limit:int=500,
    )->list[dict]:
        rows=self.store.read_jsonl(self.transition_filename,limit=max(limit*4,limit))
        if market_id:
            key=market_id.upper()
            rows=[r for r in rows if str(r.get("market_id","")).upper()==key]
        if mode:
            key=mode.upper()
            rows=[r for r in rows if str(r.get("mode","")).upper()==key]
        return rows[-limit:]

    def list(
        self,
        market_id:str|None=None,
        mode:str|None=None,
        limit:int=500,
    )->list[dict]:
        rows=self.store.read_jsonl(self.filename,limit=max(limit*4,limit))
        if market_id:
            key=market_id.upper()
            rows=[r for r in rows if str(r.get("market_id","")).upper()==key]
        if mode:
            key=mode.upper()
            rows=[r for r in rows if str(r.get("mode","")).upper()==key]
        return rows[-limit:]

    def status(self)->dict:
        rows=self.store.read_jsonl(self.filename)
        transitions=self.store.read_jsonl(self.transition_filename)
        counts={}
        for row in rows:
            key=f"{row.get('market_id')}:{row.get('mode')}"
            counts[key]=counts.get(key,0)+1
        return {
            "version":self.version,
            "count":len(rows),
            "transition_count":len(transitions),
            "counts":counts,
            "persistent":self.store.persistent_mount_detected,
            "discipline":"OBSERVATION_AND_TRANSITION_RESEARCH_ONLY_NO_TRADING_SIDE_EFFECTS",
        }

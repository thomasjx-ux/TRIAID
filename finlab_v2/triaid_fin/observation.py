from __future__ import annotations

from datetime import datetime, timezone

from .store import RunStore


class MarketObservationStore:
    version="market-observation@0.1.0"

    def __init__(self,store:RunStore)->None:
        self.store=store
        self.filename="market_observations.jsonl"
        self.index_name="market_observation_index.json"
        self.index=store.load_json(self.index_name,default={}) or {}

    def record(self,snapshot:dict)->dict:
        market=str(snapshot.get("market_id") or "").upper()
        mode=str(snapshot.get("mode") or "").upper()
        source_latest_ts=snapshot.get("source_latest_ts")
        provider=snapshot.get("provider")
        if not market or not mode or source_latest_ts is None:
            return {"recorded":False,"reason":"INVALID_SNAPSHOT"}

        key=f"{market}:{mode}"
        signature=f"{source_latest_ts}:{provider}"
        if self.index.get(key)==signature:
            return {
                "recorded":False,
                "reason":"DUPLICATE_SOURCE_TIMESTAMP",
                "market_id":market,
                "mode":mode,
                "source_latest_ts":source_latest_ts,
            }

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
        }
        self.store.append_jsonl(self.filename,row)
        self.index[key]=signature
        self.store.save_json(self.index_name,self.index)
        return {"recorded":True,"observation":row}

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
        counts={}
        for row in rows:
            key=f"{row.get('market_id')}:{row.get('mode')}"
            counts[key]=counts.get(key,0)+1
        return {
            "version":self.version,
            "count":len(rows),
            "counts":counts,
            "persistent":self.store.persistent_mount_detected,
            "discipline":"OBSERVATION_ONLY_NO_TRADING_SIDE_EFFECTS",
        }

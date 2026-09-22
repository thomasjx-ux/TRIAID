from __future__ import annotations

import calendar
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from .store import RunStore

MONTH_CODE={1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}


class PolicyExpectationCurve:
    version="policy-expectation-curve@0.1.0"
    latest_file="policy_expectation_curve_latest.json"
    history_file="policy_expectation_curve_history.jsonl"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _month_add(year:int,month:int,delta:int)->tuple[int,int]:
        z=(year*12+(month-1))+int(delta)
        return z//12,z%12+1

    @staticmethod
    def _contract_candidates(root:str,year:int,month:int,suffixes:tuple[str,...])->list[str]:
        code=MONTH_CODE[int(month)]
        yy=str(int(year))[-2:]
        stem=f"{root}{code}{yy}"
        return [stem+s for s in suffixes]

    @staticmethod
    def _fetch_chart(symbol:str,timeout:int=12)->dict:
        query=urllib.parse.urlencode({
            "range":"5d",
            "interval":"1d",
            "includeAdjustedClose":"true",
        })
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 TRIAID-FIN-POLICY-CURVE/0.1"})
        with urllib.request.urlopen(req,timeout=timeout) as response:
            payload=json.loads(response.read().decode("utf-8"))
        chart=payload.get("chart") or {}
        if chart.get("error"):
            raise RuntimeError(f"yahoo_provider_error:{symbol}:{chart.get('error')}")
        result=(chart.get("result") or [None])[0]
        if not result:
            raise RuntimeError(f"yahoo_empty_result:{symbol}")
        ts=result.get("timestamp") or []
        quote=((result.get("indicators") or {}).get("quote") or [{}])[0]
        close=quote.get("close") or []
        rows=[]
        for stamp,px in zip(ts,close):
            try:
                value=float(px)
            except Exception:
                continue
            if math.isfinite(value):
                rows.append((int(stamp),value))
        if not rows:
            raise RuntimeError(f"yahoo_no_valid_close:{symbol}")
        meta=result.get("meta") or {}
        return {
            "symbol":symbol,
            "ts":rows[-1][0],
            "price":rows[-1][1],
            "exchange":meta.get("exchangeName"),
            "instrument_type":meta.get("instrumentType"),
        }

    @classmethod
    def _resolve_contract(cls,root:str,year:int,month:int,suffixes:tuple[str,...])->dict|None:
        errors=[]
        for symbol in cls._contract_candidates(root,year,month,suffixes):
            try:
                row=cls._fetch_chart(symbol)
                row["contract_month"]=f"{year:04d}-{month:02d}"
                row["implied_rate"]=100.0-float(row["price"])
                return row
            except Exception as exc:
                errors.append(f"{symbol}:{type(exc).__name__}")
        return None

    @classmethod
    def _curve(cls,root:str,start:date,months:int,suffixes:tuple[str,...],quarterly_only:bool=False)->list[dict]:
        rows=[]
        for delta in range(int(months)):
            y,m=cls._month_add(start.year,start.month,delta)
            if quarterly_only and m not in {3,6,9,12}:
                continue
            row=cls._resolve_contract(root,y,m,suffixes)
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def _curve_metrics(rows:list[dict])->dict:
        if not rows:
            return {"available":False}
        rates=[float(x["implied_rate"]) for x in rows]
        monthly_changes=[rates[i]-rates[i-1] for i in range(1,len(rates))]
        return {
            "available":True,
            "contracts":len(rows),
            "front_implied_rate":rates[0],
            "back_implied_rate":rates[-1],
            "front_to_back_change":rates[-1]-rates[0],
            "max_step_up":max(monthly_changes) if monthly_changes else None,
            "max_step_down":min(monthly_changes) if monthly_changes else None,
            "dispersion":max(rates)-min(rates),
        }

    def run(self,force:bool=False)->dict:
        previous=self.latest()
        today=datetime.now(timezone.utc).date()
        if previous and previous.get("as_of")==today.isoformat() and not force:
            return previous

        fedfunds=self._curve("ZQ",today,15,(".CBT",".CME",""),quarterly_only=False)
        sofr3=self._curve("SR3",today,24,(".CME",""),quarterly_only=True)
        sofr1=self._curve("SR1",today,15,(".CME",""),quarterly_only=False)

        payload={
            "version":self.version,
            "as_of":today.isoformat(),
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "shadow_only":True,
            "source":"Yahoo chart quotes using CME contract month codes",
            "contract_convention":{
                "month_codes":"CME standard F,G,H,J,K,M,N,Q,U,V,X,Z",
                "implied_rate":"100 - futures price",
                "fed_funds_root":"ZQ",
                "sofr_1m_root":"SR1",
                "sofr_3m_root":"SR3",
            },
            "fed_funds_curve":fedfunds,
            "sofr_1m_curve":sofr1,
            "sofr_3m_curve":sofr3,
            "metrics":{
                "fed_funds":self._curve_metrics(fedfunds),
                "sofr_1m":self._curve_metrics(sofr1),
                "sofr_3m":self._curve_metrics(sofr3),
            },
            "data_quality":{
                "fed_funds_contracts":len(fedfunds),
                "sofr_1m_contracts":len(sofr1),
                "sofr_3m_contracts":len(sofr3),
                "term_curve_usable":len(fedfunds)>=4 or len(sofr3)>=4,
            },
            "guard":"Contract discovery is best-effort against public Yahoo symbols. Missing contract months remain missing; no interpolation is used.",
        }
        digest=self._hash(payload)
        payload["snapshot_hash"]=digest
        payload["snapshot_id"]=f"POLICY-CURVE-{today.isoformat()}-{digest[:10]}"
        self.store.save_json(self.latest_file,payload)
        history=self.history(5000)
        if not any(x.get("snapshot_id")==payload["snapshot_id"] for x in history):
            self.store.append_jsonl(self.history_file,payload)
        return payload

    def latest(self)->dict|None:
        row=self.store.load_json(self.latest_file,default={})
        return row or None

    def history(self,limit:int=100)->list[dict]:
        return self.store.read_jsonl(self.history_file,limit=limit)

    def status(self)->dict:
        latest=self.latest()
        return {
            "version":self.version,
            "latest_snapshot_id":latest.get("snapshot_id") if latest else None,
            "latest_as_of":latest.get("as_of") if latest else None,
            "data_quality":latest.get("data_quality") if latest else None,
        }

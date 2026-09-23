from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from triaid_fin.store import RunStore
from triaid_fin.tencent_cn_data import TencentCNMarketDataProvider

MARKET="HK"
TRADE_DATE="2026-09-23"
SYMBOLS=("2800.HK","2819.HK","2828.HK","3033.HK")
START_LOCAL="09:30"
END_LOCAL="10:39"
MARKER="backfills/HK_2026-09-23_0930_1039_v1.json"
STREAM="market_backfills.jsonl"
TZ=ZoneInfo("Asia/Hong_Kong")


def epoch(hhmm:str)->int:
    dt=datetime.fromisoformat(f"{TRADE_DATE}T{hhmm}:00").replace(tzinfo=TZ)
    return int(dt.timestamp())


store=RunStore()
if store.backend.exists(MARKER):
    marker=store.load_json(MARKER)
    print("TRIAID_HK_MORNING_BACKFILL_ALREADY_PRESENT",json.dumps(marker,ensure_ascii=False),flush=True)
    raise SystemExit(0)

provider=TencentCNMarketDataProvider()
series={}
errors={}
for symbol in SYMBOLS:
    try:
        row=provider.fetch_series(
            symbol,
            range_="1d",
            interval="1m",
            include_prepost=False,
            min_points=1,
            timeout=20,
        )
        series[symbol]={
            int(ts):{"close":float(px),"volume":float(vol)}
            for ts,px,vol in zip(row.ts,row.close,row.volume)
        }
    except Exception as exc:
        errors[symbol]=f"{type(exc).__name__}:{exc}"

start_ts=epoch(START_LOCAL)
end_ts=epoch(END_LOCAL)
all_ts=sorted({
    ts
    for rows in series.values()
    for ts in rows
    if start_ts<=ts<=end_ts
})

if not all_ts:
    raise RuntimeError(f"no_backfill_rows:{errors}")

generated_at=datetime.now(timezone.utc).isoformat()
written=0
coverage=[]
hashes=[]
for ts in all_ts:
    latest={}
    for symbol in SYMBOLS:
        point=series.get(symbol,{}).get(ts)
        if point is not None:
            latest[symbol]=point
    coverage_ratio=len(latest)/len(SYMBOLS)
    payload={
        "market_id":"HK",
        "mode":"REALTIME",
        "interval":"1m",
        "trade_date":TRADE_DATE,
        "source_latest_ts":ts,
        "source_local":datetime.fromtimestamp(ts,TZ).isoformat(),
        "latest":latest,
        "symbols":list(latest),
        "requested_symbols":list(SYMBOLS),
        "coverage_ratio":coverage_ratio,
        "provider":provider.version,
        "quality":"RECONSTRUCTED_BACKFILL_NON_PROSPECTIVE",
        "backfill_only":True,
        "evidence_eligible":False,
        "decision_eligible":False,
        "execution_grade":False,
        "prospective":False,
        "reconstruction_reason":"Recover HK morning market information that was available from provider history but was not continuously persisted by runtime at the time.",
        "generated_at":generated_at,
    }
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    payload["backfill_signature"]=hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    store.append_jsonl(STREAM,payload)
    written+=1
    coverage.append(coverage_ratio)
    hashes.append(payload["backfill_signature"])

summary={
    "market_id":"HK",
    "trade_date":TRADE_DATE,
    "window_local":f"{START_LOCAL}-{END_LOCAL}",
    "interval":"1m",
    "provider":provider.version,
    "requested_symbols":list(SYMBOLS),
    "series_errors":errors,
    "rows_written":written,
    "first_source_ts":all_ts[0],
    "last_source_ts":all_ts[-1],
    "first_source_local":datetime.fromtimestamp(all_ts[0],TZ).isoformat(),
    "last_source_local":datetime.fromtimestamp(all_ts[-1],TZ).isoformat(),
    "min_coverage_ratio":min(coverage),
    "mean_coverage_ratio":sum(coverage)/len(coverage),
    "backfill_only":True,
    "evidence_eligible":False,
    "decision_eligible":False,
    "prospective":False,
    "stream":STREAM,
    "generated_at":generated_at,
    "content_hash":hashlib.sha256("".join(hashes).encode("utf-8")).hexdigest(),
}
store.save_json(MARKER,summary)
print("TRIAID_HK_MORNING_BACKFILL_PASS",json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)

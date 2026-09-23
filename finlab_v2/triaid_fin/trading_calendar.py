from __future__ import annotations

from datetime import date, datetime, time as dt_time
from zoneinfo import ZoneInfo

from .market_registry import MARKET_REGISTRY, market_ids, normalize_market_id


VERSION="official-trading-calendar@0.3.0"

def _timezone(market_id:str)->str:
    return MARKET_REGISTRY.get(market_id).timezone

OFFICIAL_SOURCES={
    "US":{
        "name":"NYSE Holidays & Trading Hours",
        "url":"https://www.nyse.com/trade/hours-calendars",
        "built_in_coverage_years":[2026,2027,2028],
    },
    "CN":{
        "name":"SSE/SZSE official holiday closure notices",
        "urls":[
            "https://www.sse.com.cn/disclosure/dealinstruc/closed/list/",
            "https://www.szse.cn/disclosure/notice/general/",
        ],
        "built_in_coverage_years":[2026],
    },
    "HK":{
        "name":"HKEX Hong Kong Securities Market Holiday Schedule",
        "url":"https://www.hkex.com.hk/News/HKEX-Calendar?sc_lang=en",
        "built_in_coverage_years":[2026],
    },
}

US_CLOSED={
    2026:{
        date(2026,1,1),date(2026,1,19),date(2026,2,16),date(2026,4,3),
        date(2026,5,25),date(2026,6,19),date(2026,7,3),date(2026,9,7),
        date(2026,11,26),date(2026,12,25),
    },
    2027:{
        date(2027,1,1),date(2027,1,18),date(2027,2,15),date(2027,3,26),
        date(2027,5,31),date(2027,6,18),date(2027,7,5),date(2027,9,6),
        date(2027,11,25),date(2027,12,24),
    },
    2028:{
        date(2028,1,17),date(2028,2,21),date(2028,4,14),date(2028,5,29),
        date(2028,6,19),date(2028,7,4),date(2028,9,4),date(2028,11,23),
        date(2028,12,25),
    },
}

US_EARLY_CLOSE={
    2026:{date(2026,11,27):dt_time(13,0),date(2026,12,24):dt_time(13,0)},
    2027:{date(2027,11,26):dt_time(13,0)},
    2028:{date(2028,7,3):dt_time(13,0),date(2028,11,24):dt_time(13,0)},
}

CN_CLOSED={
    2026:{
        date(2026,1,1),date(2026,1,2),date(2026,1,3),
        date(2026,2,15),date(2026,2,16),date(2026,2,17),date(2026,2,18),
        date(2026,2,19),date(2026,2,20),date(2026,2,21),date(2026,2,22),
        date(2026,2,23),
        date(2026,4,4),date(2026,4,5),date(2026,4,6),
        date(2026,5,1),date(2026,5,2),date(2026,5,3),date(2026,5,4),
        date(2026,5,5),
        date(2026,6,19),date(2026,6,20),date(2026,6,21),
        date(2026,9,25),date(2026,9,26),date(2026,9,27),
        date(2026,10,1),date(2026,10,2),date(2026,10,3),date(2026,10,4),
        date(2026,10,5),date(2026,10,6),date(2026,10,7),
    },
}


HK_CLOSED={
    2026:{
        date(2026,1,1),
        date(2026,2,17),date(2026,2,18),date(2026,2,19),
        date(2026,4,3),date(2026,4,6),date(2026,4,7),
        date(2026,5,1),date(2026,5,25),
        date(2026,6,19),
        date(2026,7,1),
        date(2026,10,1),date(2026,10,19),
        date(2026,12,25),
    },
}

HK_EARLY_CLOSE={
    2026:{
        date(2026,2,16):dt_time(12,0),
        date(2026,12,24):dt_time(12,0),
        date(2026,12,31):dt_time(12,0),
    },
}

_SYNCED={m:{} for m in market_ids()}
_SYNC_METADATA={}


def _market(value:str)->str:
    try:
        return normalize_market_id(value)
    except KeyError as exc:
        raise ValueError(f"unsupported_market:{value}") from exc


def _official_source(market:str)->dict:
    return OFFICIAL_SOURCES.get(
        market,
        {
            "name":"REGISTERED_MARKET_OFFICIAL_CALENDAR_NOT_CONNECTED",
            "built_in_coverage_years":[],
        },
    )


def install_synced_calendar(payload:dict|None)->None:
    global _SYNCED,_SYNC_METADATA
    synced={m:{} for m in market_ids()}
    metadata={}
    if isinstance(payload,dict):
        metadata={
            "sync_version":payload.get("version"),
            "last_check_at":payload.get("last_check_at"),
            "last_success_at":payload.get("last_success_at"),
            "last_error":payload.get("last_error"),
        }
        for market in market_ids():
            years=(
                payload.get("markets",{})
                .get(market,{})
                .get("years",{})
            )
            for year_text,row in (years or {}).items():
                try:
                    year=int(year_text)
                    if not row.get("validated"):
                        continue
                    closed={date.fromisoformat(x) for x in row.get("closed",[])}
                    early={
                        date.fromisoformat(k):dt_time.fromisoformat(v)
                        for k,v in (row.get("early_close") or {}).items()
                    }
                    if not closed:
                        continue
                    synced[market][year]={
                        "closed":closed,
                        "early_close":early,
                        "source":row.get("source") or row.get("sources"),
                        "validated_at":row.get("validated_at"),
                        "validation":row.get("validation"),
                    }
                except Exception:
                    continue
    _SYNCED=synced
    _SYNC_METADATA=metadata


def built_in_years(market_id:str)->list[int]:
    market=_market(market_id)
    source={"US":US_CLOSED,"CN":CN_CLOSED,"HK":HK_CLOSED}.get(market,{})
    return sorted(source.keys())


def synced_years(market_id:str)->list[int]:
    market=_market(market_id)
    return sorted(_SYNCED.get(market,{}).keys())


def coverage_years(market_id:str)->list[int]:
    return sorted(set(built_in_years(market_id))|set(synced_years(market_id)))


def _date(value:date|datetime|str|None,market_id:str)->date:
    market=_market(market_id)
    tz=ZoneInfo(_timezone(market))
    if value is None:
        return datetime.now(tz).date()
    if isinstance(value,datetime):
        return value.astimezone(tz).date() if value.tzinfo else value.date()
    if isinstance(value,date):
        return value
    return date.fromisoformat(str(value))


def _calendar_for_year(market_id:str,year:int)->dict|None:
    market=_market(market_id)
    synced=_SYNCED.get(market,{}).get(year)
    if synced is not None:
        return {
            **synced,
            "origin":"SYNCED_OFFICIAL",
        }

    builtins={
        "US":(US_CLOSED,US_EARLY_CLOSE),
        "CN":(CN_CLOSED,{}),
        "HK":(HK_CLOSED,HK_EARLY_CLOSE),
    }
    source=builtins.get(market)
    if source and year in source[0]:
        return {
            "closed":source[0][year],
            "early_close":source[1].get(year,{}) if isinstance(source[1],dict) else {},
            "source":_official_source(market),
            "origin":"BUILT_IN_VERIFIED",
        }
    return None


def trading_day_info(
    market_id:str,
    value:date|datetime|str|None=None,
)->dict:
    market=_market(market_id)
    day=_date(value,market)
    years=coverage_years(market)
    calendar=_calendar_for_year(market,day.year)

    if calendar is None:
        return {
            "version":VERSION,
            "market_id":market,
            "date":day.isoformat(),
            "calendar_known":False,
            "is_trading_day":False,
            "reason":"CALENDAR_YEAR_UNAVAILABLE",
            "early_close":False,
            "early_close_time":None,
            "coverage_years":years,
            "calendar_origin":None,
            "official_source":_official_source(market),
        }

    if day.weekday()>=5:
        return {
            "version":VERSION,
            "market_id":market,
            "date":day.isoformat(),
            "calendar_known":True,
            "is_trading_day":False,
            "reason":"WEEKEND",
            "early_close":False,
            "early_close_time":None,
            "coverage_years":years,
            "calendar_origin":calendar["origin"],
            "official_source":calendar.get("source") or _official_source(market),
        }

    if day in calendar["closed"]:
        return {
            "version":VERSION,
            "market_id":market,
            "date":day.isoformat(),
            "calendar_known":True,
            "is_trading_day":False,
            "reason":"OFFICIAL_EXCHANGE_HOLIDAY",
            "early_close":False,
            "early_close_time":None,
            "coverage_years":years,
            "calendar_origin":calendar["origin"],
            "official_source":calendar.get("source") or _official_source(market),
        }

    early=(calendar.get("early_close") or {}).get(day)
    return {
        "version":VERSION,
        "market_id":market,
        "date":day.isoformat(),
        "calendar_known":True,
        "is_trading_day":True,
        "reason":"OFFICIAL_TRADING_DAY",
        "early_close":early is not None,
        "early_close_time":early.strftime("%H:%M") if early else None,
        "coverage_years":years,
        "calendar_origin":calendar["origin"],
        "official_source":calendar.get("source") or _official_source(market),
    }


def official_session_phase(
    market_id:str,
    now:datetime|None=None,
)->str:
    market=_market(market_id)
    tz=ZoneInfo(_timezone(market))
    current=now.astimezone(tz) if now and now.tzinfo else (
        now.replace(tzinfo=tz) if now else datetime.now(tz)
    )
    info=trading_day_info(market,current)
    if not info["calendar_known"]:
        return "CALENDAR_UNAVAILABLE"
    if not info["is_trading_day"]:
        return "CLOSED"

    schedule=dict(MARKET_REGISTRY.get(market).session_schedule or {})
    if not schedule:
        return "CALENDAR_UNAVAILABLE"

    def parse(value:str)->dt_time:
        return dt_time.fromisoformat(value)

    def contains(window:tuple[str,str], t:dt_time)->bool:
        start_t,end_t=parse(window[0]),parse(window[1])
        return start_t<=t<end_t

    if info.get("early_close") and info.get("early_close_time"):
        early=parse(str(info["early_close_time"]))
        opens=[]
        for a,b in schedule.get("OPEN",()):
            start_t,end_t=parse(a),parse(b)
            if start_t>=early:
                continue
            opens.append((a,min(end_t,early).strftime("%H:%M")))
        schedule["OPEN"]=tuple(opens)
        post=list(schedule.get("POSTCLOSE",()))
        if post:
            _,end_text=post[-1]
            schedule["POSTCLOSE"]=((early.strftime("%H:%M"),end_text),)
        else:
            schedule["POSTCLOSE"]=((early.strftime("%H:%M"),"23:59"),)
        schedule["BREAK"]=tuple(
            (a,b) for a,b in schedule.get("BREAK",())
            if parse(a)<early
        )

    t=current.time()
    order=("PREOPEN","OPEN","POSTCLOSE","BREAK") if info.get("early_close") else ("PREOPEN","OPEN","BREAK","POSTCLOSE")
    for phase in order:
        if any(contains(tuple(window),t) for window in schedule.get(phase,())):
            return phase
    return "CLOSED"


def calendar_status(market_id:str|None=None)->dict:
    markets=[_market(market_id)] if market_id else list(market_ids())
    return {
        "version":VERSION,
        "policy":"OFFICIAL_EXCHANGE_CALENDAR; AUTO_SYNCED_OFFICIAL_OVERRIDES_BUILT_IN; FAIL_CLOSED_WHEN_YEAR_UNAVAILABLE",
        "sync_metadata":dict(_SYNC_METADATA),
        "markets":{
            market:{
                "coverage_years":coverage_years(market),
                "built_in_years":built_in_years(market),
                "synced_years":synced_years(market),
                "official_source":_official_source(market),
                "today":trading_day_info(market),
                "session_phase":official_session_phase(market),
            }
            for market in markets
        },
    }

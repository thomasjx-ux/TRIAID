from __future__ import annotations

from datetime import date, datetime, time as dt_time
from zoneinfo import ZoneInfo


VERSION="official-trading-calendar@0.1.0"

MARKET_TZ={
    "US":"America/New_York",
    "CN":"Asia/Shanghai",
}

OFFICIAL_SOURCES={
    "US":{
        "name":"NYSE Holidays & Trading Hours",
        "url":"https://www.nyse.com/trade/hours-calendars",
        "coverage_years":[2026,2027,2028],
    },
    "CN":{
        "name":"SSE/SZSE 2026 official holiday closure notices",
        "urls":[
            "https://www.sse.com.cn/disclosure/dealinstruc/closed/list/",
            "https://www.szse.cn/disclosure/notice/general/",
        ],
        "coverage_years":[2026],
    },
}

US_CLOSED={
    2026:{
        date(2026,1,1),
        date(2026,1,19),
        date(2026,2,16),
        date(2026,4,3),
        date(2026,5,25),
        date(2026,6,19),
        date(2026,7,3),
        date(2026,9,7),
        date(2026,11,26),
        date(2026,12,25),
    },
    2027:{
        date(2027,1,1),
        date(2027,1,18),
        date(2027,2,15),
        date(2027,3,26),
        date(2027,5,31),
        date(2027,6,18),
        date(2027,7,5),
        date(2027,9,6),
        date(2027,11,25),
        date(2027,12,24),
    },
    2028:{
        date(2028,1,17),
        date(2028,2,21),
        date(2028,4,14),
        date(2028,5,29),
        date(2028,6,19),
        date(2028,7,4),
        date(2028,9,4),
        date(2028,11,23),
        date(2028,12,25),
    },
}

US_EARLY_CLOSE={
    2026:{
        date(2026,11,27):dt_time(13,0),
        date(2026,12,24):dt_time(13,0),
    },
    2027:{
        date(2027,11,26):dt_time(13,0),
    },
    2028:{
        date(2028,7,3):dt_time(13,0),
        date(2028,11,24):dt_time(13,0),
    },
}

CN_CLOSED={
    2026:{
        # New Year
        date(2026,1,1),date(2026,1,2),date(2026,1,3),
        # Spring Festival
        date(2026,2,15),date(2026,2,16),date(2026,2,17),
        date(2026,2,18),date(2026,2,19),date(2026,2,20),
        date(2026,2,21),date(2026,2,22),date(2026,2,23),
        # Qingming
        date(2026,4,4),date(2026,4,5),date(2026,4,6),
        # Labour Day
        date(2026,5,1),date(2026,5,2),date(2026,5,3),
        date(2026,5,4),date(2026,5,5),
        # Dragon Boat
        date(2026,6,19),date(2026,6,20),date(2026,6,21),
        # Mid-Autumn
        date(2026,9,25),date(2026,9,26),date(2026,9,27),
        # National Day
        date(2026,10,1),date(2026,10,2),date(2026,10,3),
        date(2026,10,4),date(2026,10,5),date(2026,10,6),
        date(2026,10,7),
    },
}


def _market(value:str)->str:
    key=value.upper()
    if key not in MARKET_TZ:
        raise ValueError(f"unsupported_market:{value}")
    return key


def coverage_years(market_id:str)->list[int]:
    market=_market(market_id)
    return list(OFFICIAL_SOURCES[market]["coverage_years"])


def _date(value:date|datetime|str|None,market_id:str)->date:
    market=_market(market_id)
    tz=ZoneInfo(MARKET_TZ[market])
    if value is None:
        return datetime.now(tz).date()
    if isinstance(value,datetime):
        return value.astimezone(tz).date() if value.tzinfo else value.date()
    if isinstance(value,date):
        return value
    return date.fromisoformat(str(value))


def trading_day_info(
    market_id:str,
    value:date|datetime|str|None=None,
)->dict:
    market=_market(market_id)
    day=_date(value,market)
    years=coverage_years(market)
    source=OFFICIAL_SOURCES[market]

    if day.year not in years:
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
            "official_source":source,
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
            "official_source":source,
        }

    closed=US_CLOSED if market=="US" else CN_CLOSED
    if day in closed.get(day.year,set()):
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
            "official_source":source,
        }

    early=None
    if market=="US":
        early=US_EARLY_CLOSE.get(day.year,{}).get(day)

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
        "official_source":source,
    }


def official_session_phase(
    market_id:str,
    now:datetime|None=None,
)->str:
    market=_market(market_id)
    tz=ZoneInfo(MARKET_TZ[market])
    current=now.astimezone(tz) if now and now.tzinfo else (
        now.replace(tzinfo=tz) if now else datetime.now(tz)
    )
    info=trading_day_info(market,current)
    if not info["calendar_known"]:
        return "CALENDAR_UNAVAILABLE"
    if not info["is_trading_day"]:
        return "CLOSED"

    t=current.time()
    if market=="US":
        close=dt_time.fromisoformat(info["early_close_time"]) if info["early_close"] else dt_time(16,0)
        if dt_time(4,0)<=t<dt_time(9,30):
            return "PREOPEN"
        if dt_time(9,30)<=t<close:
            return "OPEN"
        if close<=t<dt_time(20,0):
            return "POSTCLOSE"
        return "CLOSED"

    if dt_time(9,15)<=t<dt_time(9,30):
        return "PREOPEN"
    if dt_time(9,30)<=t<dt_time(11,30) or dt_time(13,0)<=t<dt_time(15,0):
        return "OPEN"
    if dt_time(11,30)<=t<dt_time(13,0):
        return "BREAK"
    if dt_time(15,0)<=t<dt_time(18,0):
        return "POSTCLOSE"
    return "CLOSED"


def calendar_status(market_id:str|None=None)->dict:
    markets=[_market(market_id)] if market_id else ["US","CN"]
    return {
        "version":VERSION,
        "policy":"OFFICIAL_EXCHANGE_CALENDAR_FAIL_CLOSED_WHEN_YEAR_UNAVAILABLE",
        "markets":{
            market:{
                "coverage_years":coverage_years(market),
                "official_source":OFFICIAL_SOURCES[market],
                "today":trading_day_info(market),
                "session_phase":official_session_phase(market),
            }
            for market in markets
        },
    }

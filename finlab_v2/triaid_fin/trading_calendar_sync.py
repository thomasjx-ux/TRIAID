from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

from .trading_calendar import install_synced_calendar


VERSION="official-trading-calendar-sync@0.1.0"

NYSE_URL="https://www.nyse.com/trade/hours-calendars"
SSE_LIST_URL="https://www.sse.com.cn/disclosure/dealinstruc/closed/list/"
SZSE_LIST_URL="https://www.szse.cn/disclosure/notice/general/"


def _norm(value:str)->str:
    return re.sub(r"\s+"," ",value or "").strip()


class _HTMLCapture(HTMLParser):
    def __init__(self)->None:
        super().__init__(convert_charrefs=True)
        self.text_parts:list[str]=[]
        self.anchors:list[tuple[str,str]]=[]
        self._href:str|None=None
        self._anchor_parts:list[str]=[]
        self.rows:list[list[str]]=[]
        self._row:list[str]|None=None
        self._cell_parts:list[str]|None=None

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=="a":
            self._href=attrs.get("href")
            self._anchor_parts=[]
        if tag=="tr":
            self._row=[]
        if tag in {"td","th"} and self._row is not None:
            self._cell_parts=[]

    def handle_data(self,data):
        if data:
            self.text_parts.append(data)
            if self._href is not None:
                self._anchor_parts.append(data)
            if self._cell_parts is not None:
                self._cell_parts.append(data)

    def handle_endtag(self,tag):
        if tag in {"td","th"} and self._cell_parts is not None and self._row is not None:
            self._row.append(_norm(" ".join(self._cell_parts)))
            self._cell_parts=None
        if tag=="tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row=None
        if tag=="a" and self._href is not None:
            self.anchors.append((self._href,_norm(" ".join(self._anchor_parts))))
            self._href=None
            self._anchor_parts=[]

    @property
    def text(self)->str:
        return _norm(" ".join(self.text_parts))


MONTHS={
    "January":1,"February":2,"March":3,"April":4,"May":5,"June":6,
    "July":7,"August":8,"September":9,"October":10,"November":11,"December":12,
}


def _parse_en_date(text:str,year:int)->date|None:
    m=re.search(
        r"\b("+"|".join(MONTHS)+r")\s+(\d{1,2})\b",
        text or "",
        flags=re.I,
    )
    if not m:
        return None
    month=next(v for k,v in MONTHS.items() if k.lower()==m.group(1).lower())
    return date(year,month,int(m.group(2)))


def parse_nyse_calendar(html:str)->dict[int,dict]:
    parser=_HTMLCapture()
    parser.feed(html)
    header_idx=None
    years:list[int]=[]
    for idx,row in enumerate(parser.rows):
        found=[]
        for cell in row:
            m=re.fullmatch(r"\D*(20\d{2})\D*",cell)
            if m:
                found.append(int(m.group(1)))
        if "Holiday" in " ".join(row) and len(found)>=2:
            header_idx=idx
            years=found
            break
    if header_idx is None:
        raise ValueError("NYSE holiday table header not found")

    out={year:{"closed":set(),"early_close":{}} for year in years}
    holiday_names={
        "New Year","Martin Luther King","Washington","Good Friday","Memorial Day",
        "Juneteenth","Independence Day","Labor Day","Thanksgiving","Christmas",
    }
    for row in parser.rows[header_idx+1:]:
        if not row:
            continue
        label=row[0]
        if not any(token.lower() in label.lower() for token in holiday_names):
            continue
        for col,year in enumerate(years, start=1):
            if col>=len(row):
                continue
            parsed=_parse_en_date(row[col],year)
            if parsed is not None:
                out[year]["closed"].add(parsed)

    text=parser.text
    phrase=re.compile(r"close early at 1:00 p\.m\.",re.I)
    for hit in phrase.finditer(text):
        snippet=text[hit.start():hit.start()+900]
        for month,day,year in re.findall(
            r"\b("+"|".join(MONTHS)+r")\s+(\d{1,2}),\s+(20\d{2})\b",
            snippet,
            flags=re.I,
        ):
            y=int(year)
            if y not in out:
                continue
            month_num=next(v for k,v in MONTHS.items() if k.lower()==month.lower())
            d=date(y,month_num,int(day))
            if d not in out[y]["closed"]:
                out[y]["early_close"][d]="13:00"

    cleaned={}
    for year,payload in out.items():
        closed=sorted(payload["closed"])
        if not 8<=len(closed)<=12:
            continue
        cleaned[year]={
            "closed":[d.isoformat() for d in closed],
            "early_close":{d.isoformat():v for d,v in sorted(payload["early_close"].items())},
        }
    if not cleaned:
        raise ValueError("NYSE calendar parsed but no year passed validation")
    return cleaned


def _inclusive_dates(year:int,sm:int,sd:int,em:int,ed:int)->set[date]:
    start=date(year,sm,sd)
    end=date(year,em,ed)
    if end<start:
        raise ValueError("holiday range ends before it starts")
    if (end-start).days>20:
        raise ValueError("holiday range unexpectedly long")
    return {start+timedelta(days=i) for i in range((end-start).days+1)}


def parse_cn_notice(text_or_html:str,year:int)->set[date]:
    parser=_HTMLCapture()
    parser.feed(text_or_html)
    text=parser.text
    if str(year) not in text or "休市" not in text:
        raise ValueError(f"CN notice does not describe {year} exchange closures")

    closed:set[date]=set()
    pattern=re.compile(
        r"(\d{1,2})月(\d{1,2})日"
        r"[^。；]{0,80}?至"
        r"(?:(\d{1,2})月)?(\d{1,2})日"
        r"[^。；]{0,80}?休市"
    )
    for m in pattern.finditer(text):
        sm=int(m.group(1));sd=int(m.group(2))
        em=int(m.group(3) or sm);ed=int(m.group(4))
        closed.update(_inclusive_dates(year,sm,sd,em,ed))

    single=re.compile(r"(?<!至)(\d{1,2})月(\d{1,2})日[^。；]{0,40}?休市")
    for m in single.finditer(text):
        try:
            closed.add(date(year,int(m.group(1)),int(m.group(2))))
        except ValueError:
            pass

    weekday_count=sum(1 for d in closed if d.weekday()<5)
    if len(closed)<15 or weekday_count<8:
        raise ValueError(
            f"CN notice parsed too few closures: total={len(closed)} weekday={weekday_count}"
        )
    return closed


def _find_notice_links(html:str,base_url:str,year:int)->list[str]:
    parser=_HTMLCapture()
    parser.feed(html)
    hits=[]
    for href,title in parser.anchors:
        if not href:
            continue
        title=_norm(title)
        if str(year) not in title or "休市安排" not in title:
            continue
        if "港股通" in title:
            continue
        url=urllib.parse.urljoin(base_url,href)
        if url not in hits:
            hits.append(url)
    return hits


@dataclass
class FetchResult:
    url:str
    body:str
    sha256:str


class TradingCalendarSync:
    version=VERSION
    state_name="official_trading_calendar_sync.json"

    def __init__(self,store,fetcher=None)->None:
        self.store=store
        self.enabled=os.getenv("TRIAID_CALENDAR_SYNC","1").lower() not in {
            "0","false","off","no"
        }
        self.interval_seconds=max(
            3600,
            int(os.getenv("TRIAID_CALENDAR_SYNC_INTERVAL_SECONDS","21600")),
        )
        self._fetcher=fetcher or self._fetch
        self.state=self.store.load_json(self.state_name,default={}) or {}
        self.state.setdefault("version",self.version)
        self.state.setdefault("markets",{})
        self.state.setdefault("last_check_at",None)
        self.state.setdefault("last_success_at",None)
        self.state.setdefault("last_error",None)
        install_synced_calendar(self.state)

    @staticmethod
    def _fetch(url:str)->FetchResult:
        req=urllib.request.Request(
            url,
            headers={
                "User-Agent":"Mozilla/5.0 TRIAID-FIN-CalendarSync/0.1",
                "Accept":"text/html,application/xhtml+xml",
                "Accept-Language":"en-US,en;q=0.9,zh-CN;q=0.8",
            },
        )
        with urllib.request.urlopen(req,timeout=10) as response:
            body=response.read().decode("utf-8","replace")
            final_url=response.geturl()
        return FetchResult(
            url=final_url,
            body=body,
            sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _page_urls(base:str,max_pages:int=2)->list[str]:
        urls=[base]
        if base.endswith("/"):
            for i in range(1,max_pages+1):
                urls.append(urllib.parse.urljoin(base,f"index_{i}.html"))
        return urls

    def _discover_cn_notice(self,base:str,year:int)->FetchResult|None:
        for page_url in self._page_urls(base):
            try:
                listing=self._fetcher(page_url)
            except Exception:
                continue
            links=_find_notice_links(listing.body,listing.url,year)
            for link in links:
                try:
                    detail=self._fetcher(link)
                    parse_cn_notice(detail.body,year)
                    return detail
                except Exception:
                    continue
        return None

    def _sync_us(self)->dict:
        fetched=self._fetcher(NYSE_URL)
        parsed=parse_nyse_calendar(fetched.body)
        now=datetime.now(timezone.utc).isoformat()
        years={}
        for year,payload in parsed.items():
            years[str(year)]={
                **payload,
                "validated":True,
                "validated_at":now,
                "validation":"NYSE_OFFICIAL_TABLE_STRUCTURE_AND_DATE_COUNT",
                "source":{
                    "name":"NYSE Holidays & Trading Hours",
                    "url":fetched.url,
                    "sha256":fetched.sha256,
                },
            }
        return {
            "status":"VERIFIED",
            "years":years,
            "last_verified_at":now,
        }

    def _sync_cn_year(self,year:int)->dict|None:
        sse=self._discover_cn_notice(SSE_LIST_URL,year)
        szse=self._discover_cn_notice(SZSE_LIST_URL,year)
        if sse is None or szse is None:
            return None
        sse_dates=parse_cn_notice(sse.body,year)
        szse_dates=parse_cn_notice(szse.body,year)
        if sse_dates!=szse_dates:
            raise ValueError(
                f"SSE/SZSE calendar mismatch for {year}: "
                f"sse_only={sorted(sse_dates-szse_dates)} "
                f"szse_only={sorted(szse_dates-sse_dates)}"
            )
        now=datetime.now(timezone.utc).isoformat()
        return {
            "closed":[d.isoformat() for d in sorted(sse_dates)],
            "early_close":{},
            "validated":True,
            "validated_at":now,
            "validation":"SSE_SZSE_OFFICIAL_CROSS_CHECK_EXACT_MATCH",
            "sources":[
                {"name":"Shanghai Stock Exchange","url":sse.url,"sha256":sse.sha256},
                {"name":"Shenzhen Stock Exchange","url":szse.url,"sha256":szse.sha256},
            ],
        }

    def _sync_cn(self)->dict:
        now_year=datetime.now(timezone.utc).year
        existing=(self.state.get("markets",{}).get("CN",{}).get("years",{}) or {})
        years=dict(existing)
        verified=[]
        missing=[]

        # Built-in 2026 is already verified. The autonomous job focuses on the
        # first uncovered future year; once promoted it no longer needs page scans.
        target=now_year+1
        if str(target) not in years:
            result=self._sync_cn_year(target)
            if result is None:
                missing.append(target)
            else:
                years[str(target)]=result
                verified.append(target)

        now=datetime.now(timezone.utc).isoformat()
        return {
            "status":"VERIFIED" if verified else "NO_NEW_OFFICIAL_YEAR",
            "years":years,
            "verified_this_run":verified,
            "not_yet_published_or_not_found":missing,
            "last_verified_at":now if verified else (
                self.state.get("markets",{}).get("CN",{}).get("last_verified_at")
            ),
        }

    def sync_once(self,force:bool=False)->dict:
        now=datetime.now(timezone.utc)
        last=self.state.get("last_check_at")
        if not force and last:
            try:
                age=(now-datetime.fromisoformat(last)).total_seconds()
                if age<self.interval_seconds:
                    return {
                        "status":"SKIPPED_INTERVAL",
                        "next_check_in_seconds":max(0,int(self.interval_seconds-age)),
                        "state":self.status(),
                    }
            except Exception:
                pass

        self.state["last_check_at"]=now.isoformat()
        errors={}
        successes=[]
        for market,syncer in (("US",self._sync_us),("CN",self._sync_cn)):
            try:
                result=syncer()
                self.state["markets"][market]=result
                successes.append(market)
            except Exception as exc:
                errors[market]=f"{type(exc).__name__}:{exc}"
                prior=self.state["markets"].get(market,{})
                self.state["markets"][market]={
                    **prior,
                    "status":"SYNC_ERROR_PRESERVED_LAST_VERIFIED",
                    "last_error":errors[market],
                }
            # Persist each market independently so a slow/unavailable second
            # source cannot lose an already-verified first result.
            self.store.save_json(self.state_name,self.state)
            install_synced_calendar(self.state)

        self.state["last_error"]=errors or None
        if successes:
            self.state["last_success_at"]=datetime.now(timezone.utc).isoformat()
        self.store.save_json(self.state_name,self.state)
        install_synced_calendar(self.state)
        return {
            "status":"SYNCED_WITH_ERRORS" if errors else "SYNCED",
            "markets":successes,
            "errors":errors,
            "state":self.status(),
        }

    def status(self)->dict:
        markets={}
        for market,payload in (self.state.get("markets") or {}).items():
            years=payload.get("years") or {}
            markets[market]={
                "status":payload.get("status"),
                "verified_years":sorted(int(y) for y in years),
                "last_verified_at":payload.get("last_verified_at"),
                "verified_this_run":payload.get("verified_this_run",[]),
                "not_yet_published_or_not_found":payload.get(
                    "not_yet_published_or_not_found",[]
                ),
                "last_error":payload.get("last_error"),
            }
        return {
            "version":self.version,
            "enabled":self.enabled,
            "interval_seconds":self.interval_seconds,
            "last_check_at":self.state.get("last_check_at"),
            "last_success_at":self.state.get("last_success_at"),
            "last_error":self.state.get("last_error"),
            "markets":markets,
            "policy":"OFFICIAL_ONLY; CN_REQUIRES_SSE_SZSE_EXACT_MATCH; PRESERVE_LAST_VERIFIED_ON_FAILURE",
        }

    async def run(self)->None:
        if not self.enabled:
            return
        while True:
            try:
                result=await asyncio.to_thread(self.sync_once,False)
                print(
                    "TRIAID_CALENDAR_SYNC",
                    result.get("status"),
                    result.get("markets"),
                    result.get("errors"),
                )
            except Exception as exc:
                print(
                    "TRIAID_CALENDAR_SYNC_ERROR",
                    f"{type(exc).__name__}:{exc}",
                )
            await asyncio.sleep(min(3600,self.interval_seconds))

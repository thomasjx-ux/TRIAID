from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_HOST=os.getenv("TRIAID_GATEWAY_UPSTREAM_HOST","triaid-fin-v2-runtime-fast2.railway.internal")
UPSTREAM_PORT=int(os.getenv("TRIAID_GATEWAY_UPSTREAM_PORT","8080"))
PORT=int(os.getenv("PORT","8080"))
TIMEOUT=float(os.getenv("TRIAID_GATEWAY_TIMEOUT_SECONDS","120"))
SOURCE_SHA=os.getenv("TRIAID_GATEWAY_SOURCE_SHA","").strip()
EXPECTED_UPSTREAM_HOST=os.getenv("TRIAID_GATEWAY_EXPECTED_UPSTREAM_HOST",UPSTREAM_HOST).strip()
EXPECTED_RUNTIME_SHA=os.getenv("TRIAID_GATEWAY_EXPECTED_RUNTIME_SHA","").strip()
AUDIT_TIMEOUT=float(os.getenv("TRIAID_GATEWAY_AUDIT_TIMEOUT_SECONDS","120"))

HOP_BY_HOP={
    "connection","proxy-connection","keep-alive","proxy-authenticate",
    "proxy-authorization","te","trailer","transfer-encoding","upgrade",
}

HOMEPAGE_BROKEN=b"return (currency==='USD'?'\\n}\\nfunction strategyLabelHtml"
HOMEPAGE_FIXED=(
    b"let prefix='';\\n"
    b" if(currency==='USD')prefix=String.fromCharCode(36);\\n"
    b" else if(currency==='CNY')prefix=String.fromCharCode(165);\\n"
    b" else if(currency==='HKD')prefix='HK'+String.fromCharCode(36);\\n"
    b" return prefix+v.toFixed(digits);\\n"
    b"}\\nfunction strategyLabelHtml"
)
RISK_QUALITY_OPEN=(
    b'<div class="riskbox">\\n'
    b'        <h3 id="riskDataQualityTitle">'
)
RISK_QUALITY_CLOSE=(
    b'        <ul class="risklist" id="riskDataGaps"></ul>\\n'
    b'      </div>'
)
RISK_QUALITY_OPEN_FIXED=(
    b'<details class="riskbox">\\n'
    b'        <summary id="riskDataQualityTitle" style="cursor:pointer;font-weight:700">'
)
RISK_QUALITY_CLOSE_FIXED=(
    b'        <ul class="risklist" id="riskDataGaps"></ul>\\n'
    b'      </details>'
)

STARTUP_AUDIT={}


def _request_upstream(path:str,timeout:float=10.0)->tuple[int,dict|str|None]:
    conn=http.client.HTTPConnection(UPSTREAM_HOST,UPSTREAM_PORT,timeout=timeout)
    try:
        conn.request(
            "GET",
            path,
            headers={
                "Accept":"application/json",
                "User-Agent":"TRIAID-GATEWAY-AUDIT/2.0",
                "Host":f"{UPSTREAM_HOST}:{UPSTREAM_PORT}",
            },
        )
        response=conn.getresponse()
        raw=response.read()
        text=raw.decode("utf-8","replace")
        ctype=(response.getheader("content-type") or "").lower()
        if "json" in ctype:
            try:
                payload=json.loads(text) if text else None
            except json.JSONDecodeError:
                payload={"_invalid_json":text[:500]}
        else:
            payload=text
        return response.status,payload
    finally:
        conn.close()


def _runtime_contract()->dict:
    rows=[]
    def check(name:str,condition:bool,detail=None):
        rows.append({"name":name,"passed":bool(condition),"detail":detail})

    check(
        "gateway_source_revision_is_commit_sha",
        bool(re.fullmatch(r"[0-9a-f]{40}",SOURCE_SHA)),
        SOURCE_SHA or None,
    )
    check(
        "gateway_upstream_is_expected_canonical_runtime",
        bool(UPSTREAM_HOST and UPSTREAM_HOST==EXPECTED_UPSTREAM_HOST),
        {"actual":UPSTREAM_HOST,"expected":EXPECTED_UPSTREAM_HOST},
    )

    try:
        health_code,health=_request_upstream("/health",timeout=10)
    except Exception as exc:
        health_code,health=0,{"error":f"{type(exc).__name__}:{exc}"}
    health_audit=health.get("release_audit") if isinstance(health,dict) else None
    check(
        "runtime_readiness",
        health_code==200 and isinstance(health,dict) and health.get("ok") is True,
        {"status":health_code,"payload":health},
    )
    check(
        "runtime_health_reports_passed_release_audit",
        isinstance(health_audit,dict) and health_audit.get("passed") is True,
        health_audit,
    )

    try:
        audit_code,audit=_request_upstream("/api/audit/status",timeout=10)
    except Exception as exc:
        audit_code,audit=0,{"error":f"{type(exc).__name__}:{exc}"}
    check(
        "runtime_release_audit_passed",
        audit_code==200
        and isinstance(audit,dict)
        and audit.get("passed") is True
        and int(audit.get("failed_check_count") or 0)==0,
        {"status":audit_code,"payload":audit},
    )

    try:
        status_code,status=_request_upstream("/api/status",timeout=10)
    except Exception as exc:
        status_code,status=0,{"error":f"{type(exc).__name__}:{exc}"}
    deployment=status.get("deployment") if isinstance(status,dict) else None
    deployment=deployment if isinstance(deployment,dict) else {}
    railway_sha=str(deployment.get("railway_git_commit_sha") or "").strip()
    check(
        "runtime_source_identity_verified",
        status_code==200
        and deployment.get("identity_verified") is True
        and bool(re.fullmatch(r"[0-9a-f]{40}",railway_sha)),
        {"status":status_code,"deployment":deployment},
    )
    if EXPECTED_RUNTIME_SHA:
        check(
            "runtime_matches_release_binding",
            railway_sha==EXPECTED_RUNTIME_SHA,
            {"actual":railway_sha or None,"expected":EXPECTED_RUNTIME_SHA},
        )

    failed=[row["name"] for row in rows if not row["passed"]]
    return {
        "audit":"TRIAID_GATEWAY_RELEASE_AUDIT_V2",
        "passed":not failed,
        "required_check_count":len(rows),
        "passed_check_count":len(rows)-len(failed),
        "failed_check_count":len(failed),
        "failed_checks":failed,
        "gateway_source_sha":SOURCE_SHA or None,
        "upstream_host":UPSTREAM_HOST,
        "upstream_port":UPSTREAM_PORT,
        "runtime_source_sha":railway_sha or None,
        "checks":rows,
        "completed_at_unix":time.time(),
    }


def startup_audit()->dict:
    deadline=time.monotonic()+AUDIT_TIMEOUT
    last=None
    while time.monotonic()<deadline:
        last=_runtime_contract()
        if last.get("passed"):
            print(
                "TRIAID_GATEWAY_RELEASE_AUDIT_PASS",
                json.dumps(
                    {
                        "required":last["required_check_count"],
                        "passed":last["passed_check_count"],
                        "failed":last["failed_check_count"],
                        "gateway_source_sha":last["gateway_source_sha"],
                        "runtime_source_sha":last["runtime_source_sha"],
                    },
                    separators=(",",":"),
                ),
                flush=True,
            )
            return last
        time.sleep(2)
    print(
        "TRIAID_GATEWAY_RELEASE_AUDIT_FAIL",
        json.dumps(last or {"failed_checks":["NO_AUDIT_RESULT"]},separators=(",",":")),
        flush=True,
    )
    raise SystemExit(1)


def _live_runtime_ready()->tuple[bool,dict]:
    try:
        code,payload=_request_upstream("/health",timeout=min(10.0,TIMEOUT))
        ok=code==200 and isinstance(payload,dict) and payload.get("ok") is True
        return ok,{"status":code,"payload":payload}
    except Exception as exc:
        return False,{"status":0,"error":f"{type(exc).__name__}:{exc}"}


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"

    def _send_json(self,status:int,payload:dict,head:bool=False)->None:
        body=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("X-TRIAID-Gateway-Source",SOURCE_SHA or "UNKNOWN")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _local_endpoint(self)->bool:
        path=self.path.split("?",1)[0]
        is_head=self.command=="HEAD"
        if path=="/health/live":
            self._send_json(
                200,
                {
                    "ok":True,
                    "service":"triaid-fin-v2-gateway",
                    "gateway_source_sha":SOURCE_SHA or None,
                    "startup_audit_passed":bool(STARTUP_AUDIT.get("passed")),
                },
                head=is_head,
            )
            return True
        if path=="/health":
            runtime_ok,runtime=_live_runtime_ready()
            ok=bool(STARTUP_AUDIT.get("passed")) and runtime_ok
            self._send_json(
                200 if ok else 503,
                {
                    "ok":ok,
                    "service":"triaid-fin-v2-gateway",
                    "gateway_source_sha":SOURCE_SHA or None,
                    "startup_audit":STARTUP_AUDIT,
                    "runtime_readiness":runtime,
                },
                head=is_head,
            )
            return True
        if path=="/api/gateway/status":
            runtime_ok,runtime=_live_runtime_ready()
            self._send_json(
                200 if runtime_ok and STARTUP_AUDIT.get("passed") else 503,
                {
                    "ok":bool(runtime_ok and STARTUP_AUDIT.get("passed")),
                    "service":"triaid-fin-v2-gateway",
                    "gateway_source_sha":SOURCE_SHA or None,
                    "upstream_host":UPSTREAM_HOST,
                    "upstream_port":UPSTREAM_PORT,
                    "startup_audit":STARTUP_AUDIT,
                    "runtime_readiness":runtime,
                },
                head=is_head,
            )
            return True
        return False

    def _proxy(self)->None:
        if self.command in {"GET","HEAD"} and self._local_endpoint():
            return

        try:
            length=int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length=0
        body=self.rfile.read(length) if length>0 else None

        headers={}
        for key,value in self.headers.items():
            lower=key.lower()
            if lower in HOP_BY_HOP or lower in {"host","content-length"}:
                continue
            headers[key]=value

        original_host=self.headers.get("Host","")
        client_ip=self.client_address[0] if self.client_address else ""
        prior_xff=self.headers.get("X-Forwarded-For")
        headers["Host"]=f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        headers["X-Forwarded-Host"]=original_host
        headers["X-Forwarded-Proto"]="https"
        headers["X-Forwarded-For"]=f"{prior_xff}, {client_ip}" if prior_xff else client_ip
        if body is not None:
            headers["Content-Length"]=str(len(body))

        conn=http.client.HTTPConnection(UPSTREAM_HOST,UPSTREAM_PORT,timeout=TIMEOUT)
        try:
            conn.request(self.command,self.path,body=body,headers=headers)
            upstream=conn.getresponse()
            payload=upstream.read()
            is_home=self.command in {"GET","HEAD"} and self.path.split("?",1)[0]=="/"
            patched=False
            quality_collapsed=False
            if is_home and upstream.status==200 and HOMEPAGE_BROKEN in payload:
                payload=payload.replace(HOMEPAGE_BROKEN,HOMEPAGE_FIXED,1)
                patched=True
            if is_home and upstream.status==200 and RISK_QUALITY_OPEN in payload and RISK_QUALITY_CLOSE in payload:
                payload=payload.replace(RISK_QUALITY_OPEN,RISK_QUALITY_OPEN_FIXED,1)
                payload=payload.replace(RISK_QUALITY_CLOSE,RISK_QUALITY_CLOSE_FIXED,1)
                quality_collapsed=True
            self.send_response(upstream.status,upstream.reason)
            for key,value in upstream.getheaders():
                lower=key.lower()
                if lower in HOP_BY_HOP or lower in {"content-length","etag","content-md5"}:
                    continue
                if is_home and lower in {"cache-control","expires","last-modified"}:
                    continue
                self.send_header(key,value)
            if is_home:
                self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("X-TRIAID-Gateway","canonical-audited-gateway-v2")
            self.send_header("X-TRIAID-Gateway-Source",SOURCE_SHA or "UNKNOWN")
            self.send_header("X-TRIAID-Homepage-Patched","1" if patched else "0")
            self.end_headers()
            if self.command!="HEAD" and payload:
                self.wfile.write(payload)
            if is_home:
                print(
                    "TRIAID_GATEWAY_HOME",
                    upstream.status,
                    f"patched={patched}",
                    f"quality_collapsed={quality_collapsed}",
                    flush=True,
                )
        except Exception as exc:
            payload={
                "detail":"TRIAID_RUNTIME_UPSTREAM_UNAVAILABLE",
                "error":f"{type(exc).__name__}:{exc}",
            }
            self._send_json(502,payload,head=self.command=="HEAD")
            print("TRIAID_GATEWAY_UPSTREAM_FAILED",type(exc).__name__,str(exc),flush=True)
        finally:
            conn.close()

    do_GET=_proxy
    do_HEAD=_proxy
    do_POST=_proxy
    do_PUT=_proxy
    do_PATCH=_proxy
    do_DELETE=_proxy
    do_OPTIONS=_proxy

    def log_message(self,format,*args):
        print("TRIAID_GATEWAY",self.address_string(),format%args,flush=True)


if __name__=="__main__":
    if "--static-check" in sys.argv:
        if not re.fullmatch(r"[0-9a-f]{40}",SOURCE_SHA):
            raise SystemExit("TRIAID_GATEWAY_STATIC_CHECK_FAIL:SOURCE_SHA")
        if not UPSTREAM_HOST or UPSTREAM_HOST!=EXPECTED_UPSTREAM_HOST:
            raise SystemExit("TRIAID_GATEWAY_STATIC_CHECK_FAIL:UPSTREAM_HOST")
        print("TRIAID_GATEWAY_STATIC_CHECK_PASS",flush=True)
        raise SystemExit(0)

    STARTUP_AUDIT=startup_audit()
    print(
        "TRIAID_GATEWAY_START",
        f"{UPSTREAM_HOST}:{UPSTREAM_PORT}",
        f"port={PORT}",
        f"source={SOURCE_SHA}",
        flush=True,
    )
    server=ThreadingHTTPServer(("0.0.0.0",PORT),GatewayHandler)
    server.serve_forever()

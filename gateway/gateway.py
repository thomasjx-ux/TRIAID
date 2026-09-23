from __future__ import annotations

import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_HOST=os.getenv("TRIAID_GATEWAY_UPSTREAM_HOST","triaid-fin-v2-runtime-fast.railway.internal")
UPSTREAM_PORT=int(os.getenv("TRIAID_GATEWAY_UPSTREAM_PORT","8080"))
PORT=int(os.getenv("PORT","8080"))
TIMEOUT=float(os.getenv("TRIAID_GATEWAY_TIMEOUT_SECONDS","120"))

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


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"

    def _proxy(self)->None:
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
            self.send_header("X-TRIAID-Gateway","runtime-fast-homepage-js-hotfix-v2")
            self.send_header("X-TRIAID-Homepage-Patched","1" if patched else "0")
            self.end_headers()
            if self.command!="HEAD" and payload:
                self.wfile.write(payload)
            if is_home:
                print("TRIAID_GATEWAY_HOME",upstream.status,f"patched={patched}",f"quality_collapsed={quality_collapsed}",flush=True)
        except Exception as exc:
            payload=json.dumps(
                {
                    "detail":"TRIAID_RUNTIME_UPSTREAM_UNAVAILABLE",
                    "error":f"{type(exc).__name__}:{exc}",
                },
                separators=(",",":"),
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("Connection","close")
            self.end_headers()
            if self.command!="HEAD":
                self.wfile.write(payload)
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
    print(
        "TRIAID_GATEWAY_START",
        f"{UPSTREAM_HOST}:{UPSTREAM_PORT}",
        f"port={PORT}",
        flush=True,
    )
    server=ThreadingHTTPServer(("0.0.0.0",PORT),GatewayHandler)
    server.serve_forever()

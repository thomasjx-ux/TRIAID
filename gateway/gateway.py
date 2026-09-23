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
            self.send_response(upstream.status,upstream.reason)
            for key,value in upstream.getheaders():
                lower=key.lower()
                if lower in HOP_BY_HOP or lower in {"content-length"}:
                    continue
                self.send_header(key,value)
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("X-TRIAID-Gateway","runtime-fast-v1")
            self.end_headers()
            if self.command!="HEAD" and payload:
                self.wfile.write(payload)
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

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT=int(os.getenv("PORT","8080"))
SERVICE_NAME=os.getenv("RAILWAY_SERVICE_NAME","retired-triaid-service")

class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"

    def _send(self,status:int,payload:dict)->None:
        raw=json.dumps(payload,separators=(",",":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        if self.command!="HEAD":
            self.wfile.write(raw)

    def do_GET(self):
        path=self.path.split("?",1)[0]
        if path in {"/health","/health/live"}:
            self._send(200,{"ok":True,"state":"RETIRED_QUARANTINED","service":SERVICE_NAME,"accepts_production_traffic":False})
            return
        self._send(410,{"ok":False,"state":"RETIRED_QUARANTINED","service":SERVICE_NAME,"detail":"THIS_SERVICE_IS_RETIRED_AND_MUST_NOT_RECEIVE_PRODUCTION_TRAFFIC"})

    do_HEAD=do_GET
    def do_POST(self):
        self._send(410,{"ok":False,"state":"RETIRED_QUARANTINED","service":SERVICE_NAME,"detail":"THIS_SERVICE_IS_RETIRED_AND_MUST_NOT_RECEIVE_PRODUCTION_TRAFFIC"})
    do_PUT=do_POST
    do_PATCH=do_POST
    do_DELETE=do_POST
    do_OPTIONS=do_POST

    def log_message(self,format,*args):
        print("TRIAID_RETIRED_SERVICE",self.address_string(),format%args,flush=True)

if __name__=="__main__":
    print("TRIAID_RETIRED_QUARANTINE_START",SERVICE_NAME,f"port={PORT}",flush=True)
    ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()

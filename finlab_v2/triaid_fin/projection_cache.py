from __future__ import annotations

from copy import deepcopy
from threading import Lock
from time import monotonic


class ReadThroughProjectionCache:
    """Small process-local, per-key single-flight cache for expensive UI reads.

    Live data is intentionally excluded. Only integrity-passing projections
    are cached; errors never become a successful stale answer.
    """

    version="read-through-projection-cache@1.1.0"

    def __init__(self,max_entries:int=48)->None:
        self.max_entries=max(1,int(max_entries))
        self._entries={}
        self._locks={}
        self._guard=Lock()

    def read(self,key,build,*,ttl_seconds:float,force:bool=False)->tuple[dict,bool]:
        ttl=max(0.0,float(ttl_seconds))
        with self._guard:
            entry=self._entries.get(key)
            if not force and entry and monotonic()-entry[0]<ttl:
                return dict(entry[1]),True
            singleflight=self._locks.setdefault(key,Lock())

        # Requests for unrelated markets do not block one another.
        with singleflight:
            with self._guard:
                entry=self._entries.get(key)
                if not force and entry and monotonic()-entry[0]<ttl:
                    return dict(entry[1]),True
            result=build()
            integrity=(result.get("integrity") or {}) if isinstance(result,dict) else {}
            if integrity.get("passed") is True:
                with self._guard:
                    # Store one immutable snapshot copy, then return only a
                    # shallow top-level copy on hits. UI routes mutate only
                    # top-level metadata (for example read_cache), so repeated
                    # deep copies of large projections are unnecessary.
                    self._entries[key]=(monotonic(),deepcopy(result))
                    if len(self._entries)>self.max_entries:
                        oldest=min(self._entries,key=lambda k:self._entries[k][0])
                        del self._entries[oldest]
            return result,False

    def invalidate(self,prefix:tuple=())->None:
        with self._guard:
            if not prefix:
                self._entries.clear()
                return
            for key in list(self._entries):
                if isinstance(key,tuple) and key[:len(prefix)]==prefix:
                    del self._entries[key]

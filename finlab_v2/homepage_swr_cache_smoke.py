from __future__ import annotations

from triaid_fin.projection_cache import ReadThroughProjectionCache

cache=ReadThroughProjectionCache()
calls={"n":0}

def build():
    calls["n"]+=1
    return {
        "integrity":{"passed":True},
        "payload":{"version":calls["n"]},
    }

first,hit=cache.read(
    ("market-page-swr","US","zh"),
    build,
    ttl_seconds=300,
    copy_mode="shallow_top",
)
assert hit is False
assert calls["n"]==1

cached,age=cache.peek(
    ("market-page-swr","US","zh"),
    copy_mode="shallow_top",
)
assert cached["payload"]["version"]==1
assert age is not None and age>=0

second,hit=cache.read(
    ("market-page-swr","US","zh"),
    build,
    ttl_seconds=300,
    copy_mode="shallow_top",
)
assert hit is True
assert calls["n"]==1

fresh,hit=cache.refresh(
    ("market-page-swr","US","zh"),
    build,
    copy_mode="shallow_top",
)
assert hit is False
assert fresh["payload"]["version"]==2
assert calls["n"]==2

# Failed projections never replace the last good cache entry.
bad,hit=cache.refresh(
    ("market-page-swr","US","zh"),
    lambda:{"integrity":{"passed":False},"payload":{"version":999}},
    copy_mode="shallow_top",
)
assert hit is False
still_good,age=cache.peek(
    ("market-page-swr","US","zh"),
    copy_mode="shallow_top",
)
assert still_good["payload"]["version"]==2

print("TRIAID_HOMEPAGE_SWR_CACHE_SMOKE_PASS",{
    "build_calls":calls["n"],
    "cached_version":still_good["payload"]["version"],
})

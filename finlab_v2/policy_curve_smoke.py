from __future__ import annotations
from datetime import date
from triaid_fin.policy_curve import PolicyExpectationCurve

c=PolicyExpectationCurve._contract_candidates("ZQ",2026,12,("",".CBT",".CME"))
assert c[0]=="ZQZ26"
assert "ZQZ26.CBT" in c
rows=[
    {"implied_rate":3.50},
    {"implied_rate":3.75},
    {"implied_rate":4.00},
    {"implied_rate":3.90},
]
m=PolicyExpectationCurve._curve_metrics(rows)
assert m["available"] is True
assert m["contracts"]==4
assert abs(m["front_to_back_change"]-0.40)<1e-12
assert abs(m["max_step_up"]-0.25)<1e-12
assert abs(m["max_step_down"]+0.10)<1e-12
print("TRIAID_POLICY_CURVE_SMOKE_PASS",{"candidates":c,"metrics":m})

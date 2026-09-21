from triaid_fin.adaptive_alpha import PROMOTION_STANDARD,promotion_gate

assert PROMOTION_STANDARD["shadow_to_pilot"]["pilot_max_risk_budget"]==0.10

good={
    "trading_days":35,
    "independent_decisions":22,
    "paired_net_excess_per_decision":0.0007,
    "excess_win_rate":0.58,
    "positive_rolling_windows_ratio":0.66,
    "max_drawdown_deterioration":0.004,
    "turnover_multiplier":1.20,
    "capacity_pass":True,
    "liquidity_pass":True,
    "risk_pass":True,
    "lookahead_or_same_bar_leakage":False,
}
assert promotion_gate(good,"shadow_to_pilot")["passed"] is True

bad=dict(good)
bad["paired_net_excess_per_decision"]=-0.0001
assert promotion_gate(bad,"shadow_to_pilot")["passed"] is False

leaky=dict(good)
leaky["lookahead_or_same_bar_leakage"]=True
assert promotion_gate(leaky,"shadow_to_pilot")["passed"] is False

primary={
    "additional_trading_days":45,
    "additional_independent_decisions":23,
    "cumulative_net_excess":0.015,
    "paired_net_excess_per_decision":0.0008,
    "excess_win_rate":0.60,
    "positive_rolling_windows_ratio":0.70,
    "max_drawdown_deterioration":0.003,
    "turnover_multiplier":1.10,
    "capacity_pass":True,
    "liquidity_pass":True,
    "risk_pass":True,
    "lookahead_or_same_bar_leakage":False,
}
assert promotion_gate(primary,"pilot_to_primary")["passed"] is True
print("TRIAID_ADAPTIVE_ALPHA_PROMOTION_SMOKE_PASS")

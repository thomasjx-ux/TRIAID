from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
for market_id in ("US","CN"):
    existing=engine.latest_run(market_id)
    latest_recovery=engine.latest_recovery_wave_decision("CN") if market_id=="CN" else None
    needs_recovery_bootstrap=(
        market_id=="CN"
        and (
            latest_recovery is None
            or latest_recovery.get("core_version")!=engine.recovery_wave_core.version
        )
    )
    latest_us_return=engine.latest_us_return_max_decision() if market_id=="US" else None
    needs_us_return_bootstrap=(
        market_id=="US"
        and (
            latest_us_return is None
            or latest_us_return.get("route_version")!=engine.us_return_max.version
        )
    )
    if existing is None or needs_recovery_bootstrap or needs_us_return_bootstrap:
        run=engine.create_pending_live_run(market_id)
        engine.execute_live(run.run_id,market_id)
        result=engine.get_run(run.run_id)
    else:
        result=existing
    print("TRIAID_FIN_V2_BOOTSTRAP",market_id,result.status,result.market.as_of,result.market.snapshot_id)
    if market_id=="US":
        us_return=engine.latest_us_return_max_decision()
        print(
            "TRIAID_US_RETURN_MAX_BOOTSTRAP",
            us_return.get("decision_id") if us_return else None,
            us_return.get("decision_status") if us_return else None,
            us_return.get("market_as_of") if us_return else None,
            bool(us_return and us_return.get("decision_hash")),
        )
        assert us_return is not None
        assert us_return.get("decision_hash")
        assert us_return.get("route_version")==engine.us_return_max.version
        assert us_return.get("capital_capacity",{}).get("capital_sleeves_usd")==[100000,1000000,10000000,100000000]
    if market_id=="CN":
        recovery=engine.latest_recovery_wave_decision("CN")
        print(
            "TRIAID_RECOVERY_WAVE_BOOTSTRAP",
            recovery.get("decision_id") if recovery else None,
            recovery.get("decision_status") if recovery else None,
            recovery.get("market_as_of") if recovery else None,
            bool(recovery and recovery.get("decision_hash")),
        )
        assert recovery is not None
        assert recovery.get("decision_hash")
        assert recovery.get("core_version")==engine.recovery_wave_core.version
    if result.strategy_group:
        state_map={s.strategy_id:s for s in result.strategy_states}
        rows=[]
        for sid in result.strategy_group.members:
            s=state_map.get(sid)
            rows.append({
                "strategy_id":sid,
                "expected_net_return":None if s is None else s.expected_net_return,
                "risk":None if s is None else s.risk,
                "uncertainty":None if s is None else s.uncertainty,
                "baseline_weight":result.strategy_group.weights.get(sid,0.0),
                "triaid_weight":result.triaid_decision.weights_after.get(sid,0.0) if result.triaid_decision else None,
            })
        print("TRIAID_FIN_V2_GROUP_DIAG",market_id,rows)

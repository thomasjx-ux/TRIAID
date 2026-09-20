from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
for market_id in ("US","CN"):
    if engine.latest_run(market_id) is None:
        run=engine.create_pending_live_run(market_id)
        engine.execute_live(run.run_id,market_id)
        result=engine.get_run(run.run_id)
    else:
        result=engine.latest_run(market_id)
    print("TRIAID_FIN_V2_BOOTSTRAP",market_id,result.status,result.market.as_of,result.market.snapshot_id)
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

from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
for market_id in ("US","CN"):
    if engine.latest_run(market_id) is not None:
        continue
    run=engine.create_pending_live_run(market_id)
    engine.execute_live(run.run_id,market_id)
    result=engine.get_run(run.run_id)
    print("TRIAID_FIN_V2_BOOTSTRAP",market_id,result.status,result.market.as_of,result.market.snapshot_id)

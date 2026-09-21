from app import daily, evolution_status, home, status, strategies

html=home()
assert "TRIAID 增益" in html
assert 'id="baseReturn"' in html
assert 'id="triaidReturn"' in html
assert 'id="gain"' in html
assert 'id="thDelta"' in html
assert "renderComparison" in html
assert "baseline_weight" in html
assert "triaid_weight" in html
assert "JSON.stringify(d,null,2)" not in html
assert "const el=id=>document.getElementById(id);" in html
assert "UI data error:" in html
assert 'id="hoverTip"' in html
assert 'class="has-tip"' in html
assert "const TIP={" in html
assert "data-tip=" in html
assert "SHADOW：只记录真实未来表现进行前瞻验证" in html
assert 'id="candidatePoolTitle"' in html
assert 'id="candidateRows"' in html
assert 'id="marketPulse"' in html
assert 'id="activityPulse"' in html
assert 'id="indexRows"' in html
assert 'id="commandLog"' in html
assert "refreshLiveWindows" in html
assert "/api/market-data/live-indicators/" in html
assert "/api/market-data/activity/" in html
assert "CN_WORST_POOL_RESCUE" in html
assert 'id="prospectivePanel"' in html
assert 'id="prospectiveStrategyRows"' in html
assert 'id="prospectiveDailyRows"' in html
assert "renderProspective" in html
assert "TRIAID冻结配置累计收益" in html
assert 'id="recoveryWavePanel"' in html
assert 'id="recoveryOpinionRows"' in html
assert 'id="recoveryReviewRows"' in html
assert 'id="recoveryPreviousMeta"' in html
assert 'id="rwthSpeed"' in html
assert "renderRecoveryWave" in html
assert "TRIAID 二阶恢复波段决策" in html
assert "当前冻结交易意见" in html
assert "cards.filter(x=>x.selected)" in html
assert "cards.filter(x=>!x.selected)" in html
assert html.index('id="liveTitle"') < html.index('id="strategyTitle"')
assert html.index('id="strategyTitle"') < html.index('id="dailyTitle"')
assert html.index('id="dailyTitle"') < html.index('id="overviewTitle"')
assert html.index('id="overviewTitle"') < html.index('id="curveTitle"')

s=status()
assert s["architecture_version"]=="fin-evolution-lab@0.9.0"
assert s["strategy_registry_count"]==33

d=daily("US")
assert d["date"]

dcn=daily("CN")
assert dcn["date"]
assert "prospective_experiment" in dcn
assert dcn["prospective_experiment"]["report_version"]=="cn-prospective-controls@0.2.0"
assert dcn["prospective_experiment"]["protocol_version"].startswith("cn-prospective-controls@")
assert dcn["prospective_experiment"]["strategy_determination"]

assert "recovery_wave" in dcn
assert dcn["recovery_wave"]["integrity"]["passed"] is True
assert dcn["recovery_wave"]["latest_decision"]["core_version"]=="recovery-wave-core@0.1.0"
assert dcn["recovery_wave"]["latest_decision"]["data_scope"]["constituent_micro_available"] is False
assert dcn["recovery_wave"]["latest_decision"]["execution_discipline"]["same_bar_execution_allowed"] is False
assert dcn["recovery_wave"]["latest_decision"]["trade_opinions"]

cards=strategies("zh","US")
assert len(cards)==29
assert any(x["selected"] for x in cards)
assert all(x["name"] for x in cards)
assert all("baseline_weight" in x and "triaid_weight" in x for x in cards)

e=evolution_status()
assert e["active_version"]
assert s["active_strategy_rules"]["US"]["version"].startswith("strategy-rules-us@")
assert s["active_strategy_rules"]["CN"]["version"].startswith("strategy-rules-cn@")

print("TRIAID_FIN_V2_UI_SMOKE_PASS")
print({
    "architecture":s["architecture_version"],
    "core":s["active_core"]["version"],
    "date":d["date"],
    "strategy_cards":len(cards),
    "selected":sum(1 for x in cards if x["selected"]),
    "ui_mode":"comparison-live-indicators-then-strategy",
})

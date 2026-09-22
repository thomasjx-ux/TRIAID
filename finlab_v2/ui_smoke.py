from app import daily, evolution_status, home, status, strategies, engine

html=home()
assert "TRIAID 相对收益差" in html
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
assert "const TABLE_HEADER_TIPS={" in html
assert "function applyTableHeaderTooltips" in html
assert "root.querySelectorAll('table th')" in html
assert "MutationObserver" in html
assert "const UI_TIPS={" in html
assert "function applyUiTooltips" in html
assert "tip-mark" in html
assert "多周期年化收益估计" in html
assert "首个正向历史窗口" in html
assert "历史收益优势速度/日" in html
assert "恢复率优势×样本支持" in html
assert "历史相似状态平均前向收益" in html
assert "累计单期超额和" in html
assert "最新数据摘要" in html
assert "TRIAID冻结综合排序" in html
assert "最大目标仓位/ADV" in html
assert "模型往返成本代理" in html
assert "模型波段净损益代理" in html
assert "通用 Core" in html
assert "已后验评价运行" in html
assert "负相对收益差比例" in html
assert "模拟成交比例" in html
assert "本轮调整" in html
assert "Return-Max权重" in html
assert "预期净回报" not in html
assert "预计恢复速度/日" not in html
assert "当前冻结交易意见" not in html
assert "预计往返成本" not in html
assert "单日ADV占比" not in html
assert "TRIAID 挽回损失" not in html
assert "TRIAID 本次产生正增益" not in html
assert html.count("<table")>=13
assert html.count("<th")>=70
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
assert "/api/market-data/strategy-context/" in html
assert "strategyMarketTip" in html
assert "strategyLabelHtml" in html
assert "market-tip-icon" in html
assert "data-strategy-id=" in html
assert "last trading day" in html
assert "CN_RETURN_MAX_CAPACITY" in html
assert "A股收益最大化主路线" in html
assert 'id="prospectivePanel"' in html
assert 'id="prospectiveStrategyRows"' in html
assert 'id="prospectiveDailyRows"' in html
assert "renderProspective" in html
assert "TRIAID冻结配置累计收益" in html
assert 'id="usReturnMaxPanel"' in html
assert 'id="usrmCapitalRows"' in html
assert 'id="usrmRealizedRows"' in html
assert "renderUSReturnMax" in html
assert "美股 Return-Max 路线" in html
assert 'id="recoveryWavePanel"' in html
assert 'id="recoveryOpinionRows"' in html
assert 'id="recoveryReviewRows"' in html
assert 'id="recoveryPreviousMeta"' in html
assert 'id="rwthSpeed"' in html
assert 'id="capitalSleeveRows"' in html
assert 'id="capitalRealizedRows"' in html
assert "四档人民币资金规模容量实验" in html
assert "renderRecoveryWave" in html
assert "TRIAID 二阶恢复波段研究" in html
assert "当前冻结研究配置意见" in html
assert "立即运行（预览）" in html
assert "不进入正式证据链" in html
assert "PREVIEW_READY" in html
assert "previewRunIds" in html
assert "&run_id=" in html
assert "cards.filter(x=>x.selected)" in html
assert "cards.filter(x=>!x.selected)" in html
assert html.index('id="liveTitle"') < html.index('id="strategyTitle"')
assert html.index('id="strategyTitle"') < html.index('id="dailyTitle"')
assert html.index('id="dailyTitle"') < html.index('id="overviewTitle"')
assert html.index('id="overviewTitle"') < html.index('id="curveTitle"')

s=status()
assert s["architecture_version"]=="fin-evolution-lab@0.13.1"
assert s["strategy_registry_count"]==33

d=daily("US")
assert d["date"]
# A clean file-backed UI smoke has no persisted live ledger by design.
# Contract-specific US Return-Max behavior is covered by us_return_max_smoke.py.
if "us_return_max" in d:
    assert d["us_return_max"]["report_version"]=="us-return-max-ledger@0.1.0"
    assert d["us_return_max"]["route_version"]==engine.us_return_max.version
    assert d["us_return_max"]["integrity"]["passed"] is True

dcn=daily("CN")
assert dcn["date"]
# Prospective and recovery report payloads appear only after persisted decisions exist.
# Their deterministic contracts are exercised in dedicated smoke tests.
if "prospective_experiment" in dcn:
    assert dcn["prospective_experiment"]["report_version"]=="cn-prospective-controls@0.3.0"
    assert dcn["prospective_experiment"]["protocol_version"].startswith("cn-prospective-controls@")
if "recovery_wave" in dcn:
    assert dcn["recovery_wave"]["integrity"]["passed"] is True
    assert dcn["recovery_wave"]["latest_decision"]["core_version"]==engine.recovery_wave_core.version
    assert dcn["recovery_wave"]["latest_decision"]["data_scope"]["constituent_micro_available"] is False
    assert dcn["recovery_wave"]["latest_decision"]["execution_discipline"]["same_bar_execution_allowed"] is False
    assert dcn["recovery_wave"]["latest_decision"]["trade_opinions"]
    assert dcn["recovery_wave"]["latest_decision"]["capital_capacity"]["enabled"] is True
    assert dcn["recovery_wave"]["latest_decision"]["capital_capacity"]["capital_sleeves_cny"]==[100000,1000000,10000000,100000000]
    assert len(dcn["recovery_wave"]["latest_decision"]["capital_capacity"]["sleeves"])==4

cards=strategies("zh","US")
assert len(cards)==29
assert all(x["name"] for x in cards)
assert all("selected" in x for x in cards)
assert all("baseline_weight" in x and "triaid_weight" in x for x in cards)
# A clean persistence backend has no latest decision, so zero cards may be selected.
# Selection rendering against persisted decisions is covered by production API/UI smoke.

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

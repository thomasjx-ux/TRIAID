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
assert html.index('id="strategyTitle"') < html.index('id="dailyTitle"')
assert html.index('id="dailyTitle"') < html.index('id="overviewTitle"')
assert html.index('id="overviewTitle"') < html.index('id="curveTitle"')

s=status()
assert s["architecture_version"]=="fin-evolution-lab@0.4.1"
assert s["strategy_registry_count"]==29

d=daily("US")
assert d["date"]

cards=strategies("zh","US")
assert len(cards)==29
assert any(x["selected"] for x in cards)
assert all(x["name"] for x in cards)
assert all("baseline_weight" in x and "triaid_weight" in x for x in cards)

e=evolution_status()
assert e["active_version"]

print("TRIAID_FIN_V2_UI_SMOKE_PASS")
print({
    "architecture":s["architecture_version"],
    "core":s["active_core"]["version"],
    "date":d["date"],
    "strategy_cards":len(cards),
    "selected":sum(1 for x in cards if x["selected"]),
    "ui_mode":"comparison-then-strategy-first",
})

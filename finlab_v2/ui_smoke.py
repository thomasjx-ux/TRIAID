from app import daily, evolution_status, home, status, strategies

html=home()
assert "join('\\n')" in html
assert "replace(/\\n/g" in html
assert "const el=id=>document.getElementById(id);" in html
assert "UI data error:" in html

s=status()
assert s["architecture_version"]=="fin-evolution-lab@0.4.1"
assert s["strategy_registry_count"]==29

d=daily("US")
assert d["date"]

cards=strategies("zh","US")
assert len(cards)==29
assert any(x["selected"] for x in cards)
assert all(x["name"] for x in cards)

e=evolution_status()
assert e["active_version"]

print("TRIAID_FIN_V2_UI_SMOKE_PASS")
print({
    "architecture":s["architecture_version"],
    "core":s["active_core"]["version"],
    "date":d["date"],
    "strategy_cards":len(cards),
    "selected":sum(1 for x in cards if x["selected"]),
})

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = (ROOT / "app.py").read_text(encoding="utf-8")

# Layer 1: inspect the raw Python source inside the embedded <script>.
# home() returns a normal triple-quoted Python string, so a single backslash
# before n would be consumed by Python and become a physical newline in the
# browser's JS string. JavaScript newline escapes therefore require an even
# number of source backslashes at this layer.
raw_start = source.find("<script>")
raw_end = source.find("</script>", raw_start)
if raw_start < 0 or raw_end <= raw_start:
    raise SystemExit("TRIAID_RENDERED_HOME_JS_FAILED:RAW_SCRIPT_NOT_FOUND")
raw_js = source[raw_start + len("<script>"):raw_end]

unsafe_escapes = []
i = 0
while i < len(raw_js) - 1:
    if raw_js[i] == "\\":
        j = i
        while j < len(raw_js) and raw_js[j] == "\\":
            j += 1
        if j < len(raw_js) and raw_js[j] == "n" and (j - i) % 2 == 1:
            line = raw_js.count("\n", 0, i) + 1
            unsafe_escapes.append(f"python_consumed_js_newline_escape@line{line}")
        i = j + 1
        continue
    i += 1

# Layer 2: ask Python's AST for the actual HTML string that home() will serve.
tree = ast.parse(source, filename="app.py")
home_html = None
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "home":
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                home_html = stmt.value.value
                break
        break

if not home_html:
    raise SystemExit("TRIAID_RENDERED_HOME_JS_FAILED:HOME_HTML_LITERAL_NOT_FOUND")

start = home_html.find("<script>")
end = home_html.find("</script>", start)
if start < 0 or end <= start:
    raise SystemExit("TRIAID_RENDERED_HOME_JS_FAILED:RENDERED_SCRIPT_NOT_FOUND")
js = home_html[start + len("<script>"):end]

# A common browser-breaking symptom from this bug is quote + physical newline
# + quote after Python has rendered the page.
broken_empty_string_newlines = re.findall(r"(['\"])\n\1", js)

checks = {
    "no_python_consumed_js_newline_escape": not unsafe_escapes,
    "no_rendered_quote_newline_quote": not broken_empty_string_newlines,
    "startup_refresh_present": "refreshAll()" in js,
    "market_clock_boot_present": "refreshMarketClocks()" in js,
    "home_summary_renderer_present": "renderHomeSummary()" in js,
    "actionable_tooltip_renderer_present": "tooltipUseGuide(raw)" in js and "uiTipUseGuide(id)" in js,
    "risk_dynamic_subscore_renderer_present": "riskSubscoreTip(key,score,report)" in js and "riskDriverSummary" in js,
    "risk_overall_context_renderer_present": "riskOverallTip(score,report)" in js,
    "risk_horizon_context_renderer_present": "riskHorizonTip(h,entry,report)" in js,
}

failed = [name for name, ok in checks.items() if not ok]
failed.extend(unsafe_escapes[:10])
if broken_empty_string_newlines:
    failed.append(f"rendered_quote_newline_quote_count={len(broken_empty_string_newlines)}")
if failed:
    raise SystemExit("TRIAID_RENDERED_HOME_JS_FAILED:" + "|".join(failed))

print(
    "TRIAID_RENDERED_HOME_JS_PASS",
    {
        "script_bytes": len(js),
        "checks": len(checks),
        "unsafe_source_escapes": len(unsafe_escapes),
    },
)

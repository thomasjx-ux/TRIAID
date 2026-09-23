from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = (ROOT / "app.py").read_text(encoding="utf-8")
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
    raise SystemExit("TRIAID_RENDERED_HOME_JS_FAILED:SCRIPT_NOT_FOUND")
js = home_html[start + len("<script>"):end]

# Browser JavaScript does not allow an unescaped physical newline inside
# ordinary single- or double-quoted strings. Python's triple-quoted HTML
# can accidentally create exactly that when a JS \n is under-escaped.
quote = None
escaped = False
line = 1
col = 0
failures = []
i = 0
while i < len(js):
    ch = js[i]
    col += 1
    if ch == "\n":
        if quote in ("'", '"'):
            failures.append(f"raw_newline_inside_{'single' if quote == chr(39) else 'double'}_quoted_js_string@{line}:{col}")
            if len(failures) >= 10:
                break
        line += 1
        col = 0
        escaped = False
        i += 1
        continue
    if quote in ("'", '"'):
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote:
            quote = None
        i += 1
        continue
    # Backtick strings may legally span lines, so only track single/double
    # quote starts here. This smoke test targets browser-breaking HTML render
    # corruption rather than implementing a full JS parser.
    if ch in ("'", '"'):
        quote = ch
        escaped = False
    i += 1

checks = {
    "no_raw_newline_in_quoted_js": not failures,
    "startup_refresh_present": "refreshAll()" in js,
    "market_clock_boot_present": "refreshMarketClocks()" in js,
    "home_summary_renderer_present": "renderHomeSummary()" in js,
    "actionable_tooltip_renderer_present": "tooltipUseGuide(raw)" in js and "uiTipUseGuide(id)" in js,
}

failed = [name for name, ok in checks.items() if not ok]
failed.extend(failures)
if failed:
    raise SystemExit("TRIAID_RENDERED_HOME_JS_FAILED:" + "|".join(failed))

print("TRIAID_RENDERED_HOME_JS_PASS", {"script_bytes": len(js), "checks": len(checks)})

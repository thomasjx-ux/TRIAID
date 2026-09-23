from __future__ import annotations

from app import home

html=home()
checks={
    "empty_state_present":'id="curveEmptyState"' in html and 'id="curveEmptyTitle"' in html and 'id="curveEmptyText"' in html,
    "plot_wrapper_present":'id="curvePlotWrap"' in html and 'style="display:none"' in html,
    "canvas_retained_for_real_data":'id="curve"' in html,
    "empty_state_is_compact":".curve-empty-state{display:flex" in html and "min-height:54px" in html,
    "no_points_hides_plot":"plot.style.display=hasPoints?'block':'none'" in html,
    "no_points_shows_empty":"empty.style.display=hasPoints?'none':'flex'" in html,
    "real_points_expand_plot":"const hasPoints=Array.isArray(points)&&points.length>0" in html,
    "human_waiting_copy":"等待首个真实后验" in html and "Awaiting first realized posterior" in html,
    "empty_chart_not_drawn":"if(!hasPoints){" in html and "return;" in html,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("TRIAID_EMPTY_POSTERIOR_LAYOUT_FAILED:"+"|".join(failed))
print("TRIAID_EMPTY_POSTERIOR_LAYOUT_PASS",{"checks":len(checks)})

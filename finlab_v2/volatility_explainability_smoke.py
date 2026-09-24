from __future__ import annotations

from pathlib import Path

HTML=Path("app.py").read_text(encoding="utf-8")

required_ids=[
    "volatilityTitleTip",
    "volExplainMoveTip",
    "volExplainRangeTip",
    "volExplainCoverageTip",
    "volExplainRatioTip",
]
for market in ("US","CN","HK"):
    required_ids.extend([
        f"volBandTip{market}",
        f"volMoveTip{market}",
        f"volRangeTip{market}",
        f"volAccuracyTip{market}",
        f"volMeaning{market}",
    ])

for marker in required_ids:
    assert f'id="{marker}"' in HTML, marker

required_copy=[
    "不是“涨X%或跌X%”的方向预测",
    "明显高于68%常表示区间偏宽",
    "大于1表示低估波动",
    "function volatilityInterpretation",
    "历史校准不是“预测涨跌正确率”",
    "怎么用：",
]
for marker in required_copy:
    assert marker in HTML, marker

assert HTML.count('class="market-tip-icon has-tip"') >= 17
assert "volatilityInterpretation(band,coverage,ratio,n,zh)" in HTML
assert "当前数据不足，不应据此调整监控或风险阈值" in HTML

print("TRIAID_VOLATILITY_EXPLAINABILITY_SMOKE_PASS")

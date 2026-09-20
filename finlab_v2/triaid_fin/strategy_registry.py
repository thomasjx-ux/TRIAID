from __future__ import annotations

from .contracts import BilingualText, StrategyDefinition

POLICY_IDS = (
    "P00_BUY_HOLD","P01_VOL10","P02_VOL15","P03_DD_GUARD","P04_TREND50",
    "P05_TREND200","P06_DUAL_TREND","P07_MOM63","P08_STRESS_BLEND","P09_SHOCK_GUARD",
    "P10_VOL20","P11_TREND20","P12_TREND100","P13_MOM20","P14_MOM126","P15_MOM252",
    "P16_REV5","P17_REV20",
    "P18_XMOM20","P19_XMOM63","P20_XMOM126","P21_XMOM252",
    "P22_LOWVOL63","P23_TREND_ROT","P24_DEFENSIVE_ROT","P25_BALANCED","P26_INVOL_BAL",
    "P27_BREADTH_ROT","P28_CASH",
)

BASE_POLICY_IDS = POLICY_IDS[:10]

FAMILIES = {
    "P00_BUY_HOLD":"market_beta","P01_VOL10":"volatility_control","P02_VOL15":"volatility_control",
    "P03_DD_GUARD":"drawdown_control","P04_TREND50":"time_series_momentum","P05_TREND200":"time_series_momentum",
    "P06_DUAL_TREND":"time_series_momentum","P07_MOM63":"time_series_momentum","P08_STRESS_BLEND":"risk_control",
    "P09_SHOCK_GUARD":"risk_control","P10_VOL20":"volatility_control","P11_TREND20":"time_series_momentum",
    "P12_TREND100":"time_series_momentum","P13_MOM20":"time_series_momentum","P14_MOM126":"time_series_momentum",
    "P15_MOM252":"time_series_momentum","P16_REV5":"short_horizon_reversal","P17_REV20":"short_horizon_reversal",
    "P18_XMOM20":"cross_asset_momentum","P19_XMOM63":"cross_asset_momentum","P20_XMOM126":"cross_asset_momentum",
    "P21_XMOM252":"cross_asset_momentum","P22_LOWVOL63":"defensive_rotation","P23_TREND_ROT":"cross_asset_trend",
    "P24_DEFENSIVE_ROT":"defensive_rotation","P25_BALANCED":"strategic_allocation","P26_INVOL_BAL":"risk_balanced_allocation",
    "P27_BREADTH_ROT":"breadth_rotation","P28_CASH":"cash",
}

NAMES = {
    "P00_BUY_HOLD":("买入并持有","Buy & Hold"),
    "P01_VOL10":("10%波动率目标","10% Volatility Target"),
    "P02_VOL15":("15%波动率目标","15% Volatility Target"),
    "P03_DD_GUARD":("回撤保护","Drawdown Guard"),
    "P04_TREND50":("50日趋势","50-Day Trend"),
    "P05_TREND200":("200日趋势","200-Day Trend"),
    "P06_DUAL_TREND":("双趋势","Dual Trend"),
    "P07_MOM63":("63日动量","63-Day Momentum"),
    "P08_STRESS_BLEND":("压力混合","Stress Blend"),
    "P09_SHOCK_GUARD":("冲击保护","Shock Guard"),
    "P10_VOL20":("20%波动率目标","20% Volatility Target"),
    "P11_TREND20":("20日趋势","20-Day Trend"),
    "P12_TREND100":("100日趋势","100-Day Trend"),
    "P13_MOM20":("20日动量","20-Day Momentum"),
    "P14_MOM126":("126日动量","126-Day Momentum"),
    "P15_MOM252":("252日动量","252-Day Momentum"),
    "P16_REV5":("5日反转","5-Day Reversal"),
    "P17_REV20":("20日反转","20-Day Reversal"),
    "P18_XMOM20":("跨资产20日动量","20-Day Cross-Asset Momentum"),
    "P19_XMOM63":("跨资产63日动量","63-Day Cross-Asset Momentum"),
    "P20_XMOM126":("跨资产126日动量","126-Day Cross-Asset Momentum"),
    "P21_XMOM252":("跨资产252日动量","252-Day Cross-Asset Momentum"),
    "P22_LOWVOL63":("63日低波动轮动","63-Day Low-Vol Rotation"),
    "P23_TREND_ROT":("趋势轮动","Trend Rotation"),
    "P24_DEFENSIVE_ROT":("防御轮动","Defensive Rotation"),
    "P25_BALANCED":("平衡配置","Balanced Allocation"),
    "P26_INVOL_BAL":("逆波动配置","Inverse-Vol Allocation"),
    "P27_BREADTH_ROT":("市场宽度轮动","Breadth Rotation"),
    "P28_CASH":("现金","Cash"),
}

SUMMARIES = {
    "P00_BUY_HOLD":("基准资产保持满仓，作为最简单的市场收益基线。","Keeps full benchmark exposure as the simplest market-return baseline."),
    "P01_VOL10":("波动升高时自动降低仓位，把年化波动控制在约10%。","Cuts exposure as volatility rises, targeting about 10% annualized volatility."),
    "P02_VOL15":("较宽松的波动控制，把年化波动目标放在约15%。","A looser volatility-control strategy targeting about 15% annualized volatility."),
    "P03_DD_GUARD":("出现明显回撤后逐级降低风险敞口。","Reduces risk exposure in steps after material drawdowns."),
    "P04_TREND50":("价格跌破50日均线时降低风险。","Reduces risk when price falls below its 50-day moving average."),
    "P05_TREND200":("长期趋势转弱并跌破200日均线时退出主要风险敞口。","Exits primary risk exposure when the long trend breaks below the 200-day average."),
    "P06_DUAL_TREND":("比较20日和100日趋势，短趋势弱于中期趋势时降仓。","Compares 20-day and 100-day trends and cuts risk when the shorter trend weakens."),
    "P07_MOM63":("63日中期动量转负时显著降低风险。","Cuts risk materially when 63-day momentum turns negative."),
    "P08_STRESS_BLEND":("把波动、回撤和趋势三个压力信号合并后分级降仓。","Combines volatility, drawdown and trend stress signals to scale exposure down."),
    "P09_SHOCK_GUARD":("遇到急跌或高波动时快速降低风险。","Rapidly cuts risk after sharp losses or unusually high volatility."),
    "P10_VOL20":("允许更高风险预算的20%波动率目标策略。","A higher-risk-budget volatility target of about 20%."),
    "P11_TREND20":("用20日均线捕捉更短周期趋势变化。","Uses a 20-day moving average to react to shorter trend changes."),
    "P12_TREND100":("用100日均线判断中期趋势并调节风险。","Uses the 100-day moving average to manage medium-term trend risk."),
    "P13_MOM20":("20日动量转负时降低风险。","Reduces risk when 20-day momentum is negative."),
    "P14_MOM126":("半年左右动量转负时降低风险。","Reduces risk when roughly six-month momentum turns negative."),
    "P15_MOM252":("一年动量转负时大幅降低风险。","Cuts risk aggressively when one-year momentum is negative."),
    "P16_REV5":("长期趋势健康时，利用5日短期超跌做反转。","Buys 5-day weakness only when the long-term trend remains healthy."),
    "P17_REV20":("长期趋势健康时，利用20日阶段性回撤做反转。","Buys 20-day weakness only when the long-term trend remains healthy."),
    "P18_XMOM20":("在风险资产中持有20日表现最强者。","Rotates to the strongest risk asset over 20 days."),
    "P19_XMOM63":("在风险资产中持有63日表现最强者。","Rotates to the strongest risk asset over 63 days."),
    "P20_XMOM126":("在风险资产中持有126日趋势最强者。","Rotates to the strongest risk asset over 126 days."),
    "P21_XMOM252":("在风险资产中持有252日长期趋势最强者。","Rotates to the strongest risk asset over 252 days."),
    "P22_LOWVOL63":("优先选择趋势仍有效且近期波动较低的风险资产。","Prefers lower-volatility risk assets whose trends remain intact."),
    "P23_TREND_ROT":("在风险资产之间选择趋势和动量最强者。","Rotates toward the risk asset with the strongest trend and momentum."),
    "P24_DEFENSIVE_ROT":("风险状态转弱时切换到债券、黄金或对应防御资产。","Rotates into bonds, gold or other defensive assets when the risk regime weakens."),
    "P25_BALANCED":("风险资产与防御资产保持长期基础配置。","Maintains a strategic long-run mix of risk and defensive assets."),
    "P26_INVOL_BAL":("按照近期波动率倒数分配资产，低波动资产获得更高权重。","Allocates inversely to recent volatility, giving lower-volatility assets more weight."),
    "P27_BREADTH_ROT":("多数风险资产趋势健康时持有强势资产，否则转防御。","Holds leading risk assets when market breadth is healthy and turns defensive otherwise."),
    "P28_CASH":("不持有风险资产，作为明确的零风险敞口选择。","Holds no risky asset exposure and serves as an explicit zero-risk option."),
}

FAMILY_CONTEXT = {
    "market_beta":(
        "适合风险资产长期上涨且无需主动防御的阶段。",
        "Works best when risky assets trend upward and active defense is unnecessary.",
        "主要风险是完整承受市场下跌和回撤。",
        "Main risk is fully participating in market declines and drawdowns.",
    ),
    "volatility_control":(
        "适合波动水平变化明显、需要自动调节风险预算的阶段。",
        "Useful when volatility changes materially and risk exposure should scale automatically.",
        "波动突然回落或快速反弹时可能降仓过多。",
        "Can remain underexposed when volatility falls quickly or markets rebound sharply.",
    ),
    "drawdown_control":(
        "适合下跌持续、回撤具有延续性的阶段。",
        "Useful when drawdowns persist rather than reverse immediately.",
        "V形反转时可能在低位降仓后错过反弹。",
        "May cut exposure near the low and miss a V-shaped rebound.",
    ),
    "time_series_momentum":(
        "适合趋势较持续、方向性较强的市场。",
        "Works best in persistent directional markets.",
        "震荡和快速反转市场容易反复失效。",
        "Can whipsaw in sideways markets and fast reversals.",
    ),
    "risk_control":(
        "适合市场压力突然升高或多个风险信号同时恶化的阶段。",
        "Useful when several stress indicators deteriorate at the same time.",
        "保护过早会牺牲正常上涨阶段的收益。",
        "Overly early protection can sacrifice upside during normal markets.",
    ),
    "short_horizon_reversal":(
        "适合长期趋势未破坏但短期出现过度回撤的阶段。",
        "Works best when the long trend is intact but short-term selling becomes excessive.",
        "如果短期下跌其实是长期趋势反转，抄底会产生损失。",
        "Can lose when apparent short-term weakness is actually the start of a larger reversal.",
    ),
    "cross_asset_momentum":(
        "适合不同资产强弱分化明显、领先资产趋势可持续的阶段。",
        "Works best when leadership across assets is clear and persistent.",
        "资产领导权快速切换时换手和追涨风险会升高。",
        "Rapid leadership changes can increase turnover and chase risk.",
    ),
    "defensive_rotation":(
        "适合风险资产走弱而防御资产仍有承接能力的阶段。",
        "Works best when risk assets weaken while defensive assets remain resilient.",
        "风险和防御资产同时下跌时保护作用会减弱。",
        "Protection weakens when both risk and defensive assets fall together.",
    ),
    "cross_asset_trend":(
        "适合跨资产趋势分化并具有持续性的阶段。",
        "Works best when cross-asset trend differences are persistent.",
        "趋势快速切换会造成频繁轮动。",
        "Fast trend changes can create frequent rotation.",
    ),
    "strategic_allocation":(
        "适合需要长期平衡风险与防御敞口的普通市场环境。",
        "Useful as a stable long-run mix across normal market conditions.",
        "极端单边行情中可能落后于更集中策略。",
        "Can lag more concentrated strategies in extreme one-way markets.",
    ),
    "risk_balanced_allocation":(
        "适合不同资产波动差异较大、希望控制单一资产风险贡献的阶段。",
        "Useful when asset volatilities differ greatly and risk concentration should be limited.",
        "低波动不等于低风险，结构突变时历史波动可能失真。",
        "Low recent volatility does not guarantee low future risk when regimes change.",
    ),
    "breadth_rotation":(
        "适合市场内部广度能够反映趋势健康程度的阶段。",
        "Useful when market breadth is informative about trend health.",
        "少数权重股主导市场时广度信号可能与指数表现背离。",
        "Breadth may diverge from the index when a few large constituents dominate.",
    ),
    "cash":(
        "适合没有足够正向机会或风险约束要求退出风险资产的阶段。",
        "Appropriate when no sufficiently attractive positive opportunity remains.",
        "主要代价是上涨市场中的机会成本。",
        "Main cost is missed upside during rising markets.",
    ),
}


def build_definitions() -> list[StrategyDefinition]:
    out=[]
    for pid in POLICY_IDS:
        zh_name,en_name=NAMES[pid]
        zh_summary,en_summary=SUMMARIES[pid]
        fam=FAMILIES[pid]
        zh_best,en_best,zh_risk,en_risk=FAMILY_CONTEXT[fam]
        out.append(
            StrategyDefinition(
                strategy_id=pid,
                version="007-migrated",
                name=BilingualText(zh=zh_name,en=en_name),
                summary=BilingualText(zh=zh_summary,en=en_summary),
                logic=BilingualText(zh=zh_summary,en=en_summary),
                best_conditions=BilingualText(zh=zh_best,en=en_best),
                main_risks=BilingualText(zh=zh_risk,en=en_risk),
            )
        )
    return out

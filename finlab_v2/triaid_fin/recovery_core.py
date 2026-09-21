from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any

from .evolution import CoreParameters


def _returns(xs: list[float]) -> list[float]:
    out=[0.0]*len(xs)
    for i in range(1,len(xs)):
        out[i]=xs[i]/xs[i-1]-1.0 if xs[i-1] else 0.0
    return out


def _momentum(xs: list[float], i: int, h: int) -> float | None:
    if i<h or xs[i-h]<=0:
        return None
    return xs[i]/xs[i-h]-1.0


def _drawdown(xs: list[float], i: int, h: int=252) -> float:
    lo=max(0,i+1-h)
    peak=max(xs[lo:i+1])
    return xs[i]/peak-1.0 if peak else 0.0


def _volatility(xs: list[float], i: int, h: int) -> float:
    rs=_returns(xs)
    lo=max(1,i+1-h)
    sample=rs[lo:i+1]
    return pstdev(sample)*math.sqrt(252) if len(sample)>1 else 0.0


def _volume_ratio(volume: list[float], i: int, h: int=20) -> float | None:
    if i<0 or not volume:
        return None
    lo=max(0,i+1-h)
    sample=[float(x) for x in volume[lo:i+1] if float(x)>0]
    if not sample or float(volume[i])<=0:
        return None
    avg=sum(sample)/len(sample)
    return float(volume[i])/avg if avg>0 else None


def _median_abs_deviation(values: list[float]) -> float:
    if not values:
        return 1.0
    m=median(values)
    mad=median([abs(x-m) for x in values])
    return mad if mad>1e-12 else max(1e-6,abs(m)*0.05,1e-4)


def _normalize_budget_capped(raw: dict[str,float], budget: float, cap: float) -> dict[str,float]:
    budget=max(0.0,min(1.0,float(budget)))
    cap=max(0.0,min(1.0,float(cap)))
    positive={k:max(0.0,float(v)) for k,v in raw.items() if float(v)>0}
    out={k:0.0 for k in raw}
    active=set(positive)
    remaining=budget
    while active and remaining>1e-12:
        total=sum(positive[k] for k in active)
        if total<=0:
            break
        tentative={k:remaining*positive[k]/total for k in active}
        capped=[k for k,w in tentative.items() if w>cap]
        if not capped:
            for k,w in tentative.items():
                out[k]=w
            remaining=0.0
            break
        for k in capped:
            out[k]=cap
            remaining-=cap
            active.remove(k)
    return {k:round(v,8) for k,v in out.items()}


class RecoveryWaveCore:
    """Second-order recovery / wave-trading shadow core.

    Layer 1: observable product micro-state from each tracked ETF's own price/volume history.
    Layer 2: cross-product recovery opportunity ranking and a prospective position path.

    The current market feed does NOT contain constituent-level index internals. That limitation
    is carried explicitly in every decision and must not be silently re-labelled as constituent
    microstructure evidence.
    """

    version="recovery-wave-core@0.1.1"
    interface_version="recovery-wave-contract@1"
    horizons=(3,5,10,20)
    analog_count=20
    analog_min_separation_days=20
    per_product_cap=0.28

    def __init__(self, params: CoreParameters) -> None:
        self.params=params

    @staticmethod
    def _features(close: list[float], volume: list[float], i: int) -> dict[str,float]:
        mom5=float(_momentum(close,i,5) or 0.0)
        mom20=float(_momentum(close,i,20) or 0.0)
        mom63=float(_momentum(close,i,63) or 0.0)
        vol20=float(_volatility(close,i,20))
        vol63=float(_volatility(close,i,63))
        volume_ratio=_volume_ratio(volume,i,20)
        return {
            "drawdown_252":float(_drawdown(close,i,252)),
            "momentum_5":mom5,
            "momentum_20":mom20,
            "momentum_63":mom63,
            "short_vs_medium_acceleration":mom5-mom20*(5.0/20.0),
            "medium_vs_long_acceleration":mom20-mom63*(20.0/63.0),
            "volatility_20":vol20,
            "volatility_63":vol63,
            "volatility_acceleration":vol20-vol63,
            "volume_ratio_20":float(volume_ratio if volume_ratio is not None else 1.0),
        }

    @staticmethod
    def _direction(features: dict[str,float]) -> str:
        a=float(features.get("short_vs_medium_acceleration",0.0))
        b=float(features.get("medium_vs_long_acceleration",0.0))
        if a>0 and b>0:
            return "IMPROVING_FAST"
        if a>0 or b>0:
            return "IMPROVING"
        if a<0 and b<0:
            return "DETERIORATING"
        return "MIXED"

    def _analog_forecast(
        self,
        close: list[float],
        volume: list[float],
        current: dict[str,float],
    ) -> dict[str,Any]:
        n=len(close)
        max_h=max(self.horizons)
        start=252
        stop=n-max_h
        if stop<=start:
            return {
                "available":False,
                "reason":"INSUFFICIENT_HISTORY",
                "candidate_count":0,
                "selected_analogs":0,
                "horizons":{},
            }

        candidate_features=[]
        for i in range(start,stop):
            candidate_features.append((i,self._features(close,volume,i)))
        feature_names=list(current)
        scales={
            name:_median_abs_deviation([f[name] for _,f in candidate_features])
            for name in feature_names
        }
        distances=[]
        for i,f in candidate_features:
            distance=sum(abs(current[name]-f[name])/scales[name] for name in feature_names)/len(feature_names)
            distances.append((distance,i))
        distances.sort(key=lambda x:(x[0],x[1]))

        selected=[]
        for distance,i in distances:
            if all(abs(i-j)>=self.analog_min_separation_days for _,j in selected):
                selected.append((distance,i))
            if len(selected)>=self.analog_count:
                break

        horizon_stats={}
        all_indexes=[i for i,_ in candidate_features]
        for h in self.horizons:
            analog_returns=[close[i+h]/close[i]-1.0 for _,i in selected if close[i]>0]
            base_returns=[close[i+h]/close[i]-1.0 for i in all_indexes if close[i]>0]
            if not analog_returns or not base_returns:
                continue
            hit=sum(1 for x in analog_returns if x>0)/len(analog_returns)
            base_hit=sum(1 for x in base_returns if x>0)/len(base_returns)
            analog_mean=mean(analog_returns)
            base_mean=mean(base_returns)
            horizon_stats[str(h)]={
                "horizon_days":h,
                "analog_samples":len(analog_returns),
                "historical_positive_rate":hit,
                "unconditional_positive_rate":base_hit,
                "positive_rate_edge":hit-base_hit,
                "historical_mean_forward_return":analog_mean,
                "unconditional_mean_forward_return":base_mean,
                "mean_return_edge":analog_mean-base_mean,
                "historical_median_forward_return":median(analog_returns),
            }

        return {
            "available":bool(horizon_stats),
            "reason":"OK" if horizon_stats else "NO_VALID_FORWARD_WINDOWS",
            "candidate_count":len(candidate_features),
            "selected_analogs":len(selected),
            "analog_selection":"nearest standardized historical product states; robust MAD scaling; minimum 20-day separation; fixed max 20 analogs",
            "horizons":horizon_stats,
        }

    @staticmethod
    def _depth_percentile(current_dd: float, historical_dd: list[float]) -> float:
        if not historical_dd:
            return 0.0
        return sum(1 for x in historical_dd if x>=current_dd)/len(historical_dd)

    @staticmethod
    def _rank(values: dict[str,float], descending: bool=True) -> dict[str,int]:
        ordered=sorted(values,key=lambda k:((-values[k]) if descending else values[k],k))
        return {k:i+1 for i,k in enumerate(ordered)}

    def decide(
        self,
        panel: Any,
        regime: str | None,
        previous_decision: dict | None = None,
        input_phase: str | None = None,
    ) -> dict:
        risk_assets=[a for a in panel.spec.risk_assets if a in panel.close]
        if not risk_assets:
            raise ValueError("RecoveryWaveCore requires at least one tracked risk product.")
        current_i=len(panel.ts)-1
        if current_i<252:
            raise ValueError("RecoveryWaveCore requires at least 253 daily observations.")

        first_order={}
        raw_scores={}
        chosen_horizons={}
        certainty_values={}
        gain_values={}
        depth_values={}
        speed_values={}

        for symbol in risk_assets:
            close=[float(x) for x in panel.close[symbol]]
            volume=[float(x) for x in panel.volume.get(symbol,[0.0]*len(close))]
            features=self._features(close,volume,current_i)
            analog=self._analog_forecast(close,volume,features)
            hist_dd=[
                _drawdown(close,i,252)
                for i in range(252,max(253,len(close)-max(self.horizons)))
            ]
            depth=self._depth_percentile(features["drawdown_252"],hist_dd)
            chosen=None
            if analog.get("available"):
                for h in self.horizons:
                    row=(analog.get("horizons") or {}).get(str(h))
                    if not row:
                        continue
                    if (
                        float(row["positive_rate_edge"])>0
                        and float(row["mean_return_edge"])>0
                        and float(row["historical_median_forward_return"])>0
                    ):
                        chosen=row
                        break
            if chosen:
                support=min(1.0,float(chosen["analog_samples"])/float(self.analog_count))
                certainty=max(0.0,float(chosen["positive_rate_edge"]))*support
                gain=max(0.0,float(chosen["mean_return_edge"]))
                h=int(chosen["horizon_days"])
                speed=gain/max(1,h)
                raw=depth*certainty*gain*(20.0/max(1,h))
                chosen_horizons[symbol]=h
            else:
                certainty=0.0
                gain=0.0
                speed=0.0
                raw=0.0
                chosen_horizons[symbol]=999

            first_order[symbol]={
                "symbol":symbol,
                "state_direction":self._direction(features),
                "features":features,
                "drawdown_depth_percentile":depth,
                "analog_forecast":analog,
                "expected_reversal_horizon_days":None if chosen is None else int(chosen["horizon_days"]),
                "historical_recovery_edge":certainty,
                "expected_recovery_return_edge":gain,
                "expected_recovery_velocity_per_day":speed,
            }
            raw_scores[symbol]=raw
            certainty_values[symbol]=certainty
            gain_values[symbol]=gain
            depth_values[symbol]=depth
            speed_values[symbol]=speed

        depth_rank=self._rank(depth_values,True)
        certainty_rank=self._rank(certainty_values,True)
        gain_rank=self._rank(gain_values,True)
        speed_rank=self._rank(speed_values,True)
        borda={
            s:depth_rank[s]+certainty_rank[s]+gain_rank[s]+speed_rank[s]
            for s in risk_assets
        }
        opportunity_rank=self._rank(borda,False)

        risk_off=any(
            token in str(regime or "").lower()
            for token in ("risk_off","stress","bear","shock","high_vol")
        )
        base_risk_budget=float(self.params.risk_off_multiplier) if risk_off else 1.0
        # Certainty must govern both selection and total capital at risk.
        # This avoids converting a weak but positive rank signal into a full risk budget.
        evidence_strength=max(certainty_values.values()) if certainty_values else 0.0
        effective_risk_budget=base_risk_budget*max(0.0,min(1.0,evidence_strength))
        targets=_normalize_budget_capped(raw_scores,effective_risk_budget,self.per_product_cap)
        prev_targets={
            str(x.get("symbol")):float(x.get("target_weight") or 0.0)
            for x in (previous_decision or {}).get("trade_opinions",[])
            if x.get("symbol")
        }

        opinions=[]
        for symbol in sorted(risk_assets,key=lambda s:(opportunity_rank[s],s)):
            target=float(targets.get(symbol,0.0))
            previous=float(prev_targets.get(symbol,0.0))
            delta=round(target-previous,8)
            if previous<=0 and target>0:
                action="INITIATE"
            elif previous>0 and target>previous:
                action="ADD"
            elif previous>0 and target==0:
                action="EXIT"
            elif target<previous:
                action="REDUCE"
            elif target>0:
                action="HOLD"
            else:
                action="WAIT"
            state=first_order[symbol]
            horizon=state["expected_reversal_horizon_days"]
            horizon_row=(state["analog_forecast"].get("horizons") or {}).get(str(horizon)) if horizon else None
            opinions.append({
                "symbol":symbol,
                "opportunity_rank":opportunity_rank[symbol],
                "action":action,
                "previous_target_weight":previous,
                "target_weight":target,
                "suggested_weight_change":delta,
                "expected_reversal_horizon_days":horizon,
                "historical_recovery_edge":state["historical_recovery_edge"],
                "historical_positive_rate":horizon_row.get("historical_positive_rate") if horizon_row else None,
                "unconditional_positive_rate":horizon_row.get("unconditional_positive_rate") if horizon_row else None,
                "expected_forward_return":horizon_row.get("historical_mean_forward_return") if horizon_row else None,
                "expected_forward_return_edge":horizon_row.get("mean_return_edge") if horizon_row else None,
                "expected_recovery_velocity_per_day":state["expected_recovery_velocity_per_day"],
                "drawdown_252":state["features"]["drawdown_252"],
                "drawdown_depth_percentile":state["drawdown_depth_percentile"],
                "state_direction":state["state_direction"],
                "analog_samples":state["analog_forecast"].get("selected_analogs",0),
                "research_only":True,
                "broker_order_generated":False,
                "rationale_zh":(
                    f"当前回撤 {state['features']['drawdown_252']:.2%}，回撤深度历史分位 {state['drawdown_depth_percentile']:.1%}；"
                    + (
                        f"历史相似状态最早在 {horizon} 个交易日窗口同时出现正恢复率优势、正平均收益优势和正中位收益。"
                        if horizon else
                        "历史相似状态尚未形成同时满足恢复率、平均收益和中位收益为正的前瞻窗口，因此不建议建立恢复仓位。"
                    )
                ),
                "rationale_en":(
                    f"Current drawdown is {state['features']['drawdown_252']:.2%} at a {state['drawdown_depth_percentile']:.1%} historical depth percentile. "
                    + (
                        f"The earliest historical-analog window with positive hit-rate edge, mean-return edge and median forward return is {horizon} trading days."
                        if horizon else
                        "Historical analogs do not yet show a forward window with simultaneously positive hit-rate edge, mean-return edge and median return."
                    )
                ),
            })

        return {
            "core_version":self.version,
            "interface_version":self.interface_version,
            "market_id":panel.spec.market_id,
            "market_as_of":None,
            "source_latest_ts":panel.ts[-1],
            "input_phase":input_phase,
            "decision_status":"PROVISIONAL_INTRADAY" if str(input_phase or "").upper() in {"PREOPEN","OPEN","BREAK"} else "DAILY_FROZEN",
            "regime":regime,
            "research_only":True,
            "broker_execution_enabled":False,
            "data_scope":{
                "constituent_micro_available":False,
                "micro_scope":"TRACKED_PRODUCT_PRICE_VOLUME_ONLY",
                "tracked_products":risk_assets,
                "defensive_products":[a for a in panel.spec.defensive_assets if a in panel.close],
                "limitation":"No constituent-level index breadth/order-flow feed is currently connected; product-level price/volume state must not be described as constituent microstructure.",
            },
            "execution_discipline":{
                "same_bar_execution_allowed":False,
                "execution_rule":"DECISION_AT_T_APPLIES_FROM_NEXT_COMPLETE_TRADABLE_BAR",
                "position_path":"WAIT -> INITIATE -> ADD/HOLD -> REDUCE -> EXIT, recomputed prospectively at each new frozen state",
            },
            "method":{
                "layer_1":"PRODUCT_MICRO_STATE",
                "layer_2":"CROSS_PRODUCT_RECOVERY_OPPORTUNITY",
                "forecast":"HISTORICAL_NEAREST_STATE_ANALOGS_NO_FUTURE_LEAKAGE",
                "horizons_trading_days":list(self.horizons),
                "analog_count_max":self.analog_count,
                "analog_min_separation_days":self.analog_min_separation_days,
                "allocation":"certainty-first allocation: total capital at risk is scaled by strongest prospective recovery evidence, then distributed by cross-product recovery score; existing TRIAID risk-off multiplier is an upper bound; 28% per-product cap",
            },
            "first_order_states":first_order,
            "second_order":{
                "capital_discipline":"TOTAL_RISK_BUDGET_SCALED_BY_STRONGEST_PROSPECTIVE_RECOVERY_EVIDENCE",
                "base_risk_budget":base_risk_budget,
                "evidence_strength":evidence_strength,
                "effective_risk_budget":effective_risk_budget,
                "opportunity_ranking":[
                    {
                        "symbol":s,
                        "rank":opportunity_rank[s],
                        "borda_rank_sum":borda[s],
                        "depth_rank":depth_rank[s],
                        "certainty_rank":certainty_rank[s],
                        "expected_gain_rank":gain_rank[s],
                        "recovery_speed_rank":speed_rank[s],
                    }
                    for s in sorted(risk_assets,key=lambda x:(opportunity_rank[x],x))
                ],
                "risk_budget":effective_risk_budget,
                "risk_off_detected":risk_off,
            },
            "trade_opinions":opinions,
            "cash_residual_weight":max(0.0,round(1.0-sum(float(x["target_weight"]) for x in opinions),8)),
        }

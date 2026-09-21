from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any

from .capital_capacity import CapitalCapacityLayer
from .evolution import CoreParameters
from .adaptive_alpha import cn_soft_recovery_shadow


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

    version="recovery-wave-core@0.4.0"
    interface_version="recovery-wave-contract@1"
    horizons=(3,5,10,20)
    analog_count=20
    analog_min_separation_days=20
    per_product_cap=0.28

    def __init__(self, params: CoreParameters) -> None:
        self.params=params
        self.capital_capacity=CapitalCapacityLayer()

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

        # Deterministically split nearest historical analogues into separate
        # horizon-selection and validation sets. The same analogue outcomes must
        # not both choose the horizon and supply the reported return estimate.
        ordered_selected=sorted(selected,key=lambda x:x[1])
        selection_analogs=ordered_selected[::2]
        validation_analogs=ordered_selected[1::2]
        horizon_stats={}
        all_indexes=[i for i,_ in candidate_features]
        for h in self.horizons:
            selection_returns=[close[i+h]/close[i]-1.0 for _,i in selection_analogs if close[i]>0]
            validation_returns=[close[i+h]/close[i]-1.0 for _,i in validation_analogs if close[i]>0]
            base_returns=[close[i+h]/close[i]-1.0 for i in all_indexes if close[i]>0]
            if not selection_returns or not validation_returns or not base_returns:
                continue
            base_hit=sum(1 for x in base_returns if x>0)/len(base_returns)
            base_mean=mean(base_returns)
            sel_hit=sum(1 for x in selection_returns if x>0)/len(selection_returns)
            val_hit=sum(1 for x in validation_returns if x>0)/len(validation_returns)
            sel_mean=mean(selection_returns)
            val_mean=mean(validation_returns)
            horizon_stats[str(h)]={
                "horizon_days":h,
                "analog_samples":len(selection_returns)+len(validation_returns),
                "selection_samples":len(selection_returns),
                "validation_samples":len(validation_returns),
                "selection_positive_rate":sel_hit,
                "validation_positive_rate":val_hit,
                "unconditional_positive_rate":base_hit,
                "selection_positive_rate_edge":sel_hit-base_hit,
                "validation_positive_rate_edge":val_hit-base_hit,
                "selection_mean_forward_return":sel_mean,
                "validation_mean_forward_return":val_mean,
                "unconditional_mean_forward_return":base_mean,
                "selection_mean_return_edge":sel_mean-base_mean,
                "validation_mean_return_edge":val_mean-base_mean,
                "selection_median_forward_return":median(selection_returns),
                "validation_median_forward_return":median(validation_returns),
                # Backward-compatible fields now deliberately point only to the
                # validation half, not to the sample used to select the horizon.
                "historical_positive_rate":val_hit,
                "positive_rate_edge":val_hit-base_hit,
                "historical_mean_forward_return":val_mean,
                "mean_return_edge":val_mean-base_mean,
                "historical_median_forward_return":median(validation_returns),
            }

        return {
            "available":bool(horizon_stats),
            "reason":"OK" if horizon_stats else "NO_VALID_FORWARD_WINDOWS",
            "candidate_count":len(candidate_features),
            "selected_analogs":len(selected),
            "selection_analogs":len(selection_analogs),
            "validation_analogs":len(validation_analogs),
            "analog_selection":"nearest standardized historical product states; robust MAD scaling; minimum 20-day separation; fixed max 20 analogs; chronological alternating split separates horizon selection from validation",
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
                        float(row["selection_positive_rate_edge"])>0
                        and float(row["selection_mean_return_edge"])>0
                        and float(row["selection_median_forward_return"])>0
                        and float(row["validation_positive_rate_edge"])>0
                        and float(row["validation_mean_return_edge"])>0
                        and float(row["validation_median_forward_return"])>0
                    ):
                        chosen=row
                        break
            if chosen:
                validation_target=max(1,self.analog_count//2)
                support=min(1.0,float(chosen["validation_samples"])/float(validation_target))
                certainty=max(0.0,float(chosen["validation_positive_rate_edge"]))*support
                gain=max(0.0,float(chosen["validation_mean_return_edge"]))
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

        soft_recovery_shadow=cn_soft_recovery_shadow(first_order,regime)
        soft_symbols=list(soft_recovery_shadow.get("eligible_symbols") or [])
        soft_budget=float(soft_recovery_shadow.get("max_shadow_probe_budget") or 0.0)
        completed_i=self.capital_capacity._completed_index(panel,input_phase)
        soft_products=[]
        soft_entry_cost=0.0
        soft_liquidity_pass=True
        soft_capacity_pass=True
        if soft_symbols and soft_budget>0:
            per_symbol_weight=soft_budget/len(soft_symbols)
            for symbol in soft_symbols:
                adv=self.capital_capacity._adv_notional(panel,symbol,completed_i)
                notional=float(panel.spec.reference_capital)*per_symbol_weight
                liquidity_ok=adv>0
                participation=(notional/adv) if liquidity_ok else None
                capacity_ok=bool(
                    liquidity_ok
                    and participation is not None
                    and participation<=float(panel.spec.max_participation_adv)
                )
                soft_liquidity_pass=soft_liquidity_pass and liquidity_ok
                soft_capacity_pass=soft_capacity_pass and capacity_ok
                planned=(
                    min(float(participation),float(panel.spec.max_participation_adv))
                    if participation is not None else float(panel.spec.max_participation_adv)
                )
                bps=self.capital_capacity._execution_bps(
                    planned,
                    float(panel.spec.base_cost_bps),
                    float(panel.spec.impact_coefficient_bps),
                )
                cost=notional*float(bps["all_in_bps_per_side"])/10000.0
                soft_entry_cost+=cost
                soft_products.append({
                    "symbol":symbol,
                    "pilot_weight_on_total_capital":per_symbol_weight,
                    "pilot_notional_cny":notional,
                    "adv20_notional_cny":adv,
                    "participation_adv":participation,
                    "capacity_ok":capacity_ok,
                    "liquidity_ok":liquidity_ok,
                    "estimated_entry_cost_cny":cost,
                    "all_in_bps_per_side":float(bps["all_in_bps_per_side"]),
                })
        soft_recovery_shadow["pilot_execution_check"]={
            "starting_reference_capital_cny":float(panel.spec.reference_capital),
            "pilot_risk_budget":soft_budget,
            "risk_pass":bool(soft_symbols and soft_budget<=0.05),
            "liquidity_pass":bool(soft_symbols and soft_liquidity_pass),
            "capacity_pass":bool(soft_symbols and soft_capacity_pass),
            "pilot_turnover_fraction":soft_budget,
            "turnover_multiplier":(
                soft_budget/0.10 if soft_budget>0 else None
            ),
            "estimated_entry_cost_cny":soft_entry_cost,
            "estimated_entry_cost_fraction_of_total_capital":(
                soft_entry_cost/float(panel.spec.reference_capital)
                if float(panel.spec.reference_capital)>0 else None
            ),
            "products":soft_products,
            "semantics":"SHADOW SOFT-RECOVERY EXECUTION CHECK AT ITS CAPPED PROBE BUDGET; NOT A BROKER FILL",
        }
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
                "analog_selection_samples":horizon_row.get("selection_samples") if horizon_row else None,
                "analog_validation_samples":horizon_row.get("validation_samples") if horizon_row else None,
                "research_only":True,
                "broker_order_generated":False,
                "rationale_zh":(
                    f"当前回撤 {state['features']['drawdown_252']:.2%}，回撤深度历史分位 {state['drawdown_depth_percentile']:.1%}；"
                    + (
                        f"历史相似状态最早在 {horizon} 个交易日窗口中，独立的选择半样本和验证半样本都同时出现正恢复率优势、正平均收益优势和正中位收益。"
                        if horizon else
                        "历史相似状态尚未形成同时满足恢复率、平均收益和中位收益为正的前瞻窗口，因此不建议建立恢复仓位。"
                    )
                ),
                "rationale_en":(
                    f"Current drawdown is {state['features']['drawdown_252']:.2%} at a {state['drawdown_depth_percentile']:.1%} historical depth percentile. "
                    + (
                        f"The earliest historical-analog window where separate selection and validation halves both have positive hit-rate edge, mean-return edge and median forward return is {horizon} trading days."
                        if horizon else
                        "Historical analogs do not yet show a forward window with simultaneously positive hit-rate edge, mean-return edge and median return."
                    )
                ),
            })

        capital_capacity=self.capital_capacity.build(panel,opinions,input_phase)

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
            "soft_recovery_shadow":soft_recovery_shadow,
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
                "forecast":"HISTORICAL_NEAREST_STATE_ANALOGS_SPLIT_SELECTION_VALIDATION_NO_CURRENT_FUTURE_LEAKAGE",
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
            "capital_capacity":capital_capacity,
            "cash_residual_weight":max(0.0,round(1.0-sum(float(x["target_weight"]) for x in opinions),8)),
        }

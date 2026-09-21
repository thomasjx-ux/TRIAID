from __future__ import annotations

import math
from copy import deepcopy
from statistics import mean
from typing import Any


CAPITAL_SLEEVES_CNY=(100_000.0,1_000_000.0,10_000_000.0,100_000_000.0)


class CapitalCapacityLayer:
    """Capital-size controlled execution/capacity layer for Recovery Wave.

    The four CN sleeves start from cash at the same frozen decision. Signal and target weights
    are identical; capital size is the only experimental variable.

    Expected execution cost reuses the existing MarketSpec cost contract:
      per-side bps = base_cost_bps + impact_coefficient_bps * sqrt(participation_of_ADV)

    If a target exceeds max_participation_adv, the expected plan is split across days.
    Realized review uses each future day's observed notional-turnover proxy and activates
    fills only for the following complete bar to avoid same-day look-ahead.
    """

    version="capital-capacity-layer@0.1.0"
    experiment_version="capital-sleeves-cn@0.1.0"
    sleeves=CAPITAL_SLEEVES_CNY
    adv_lookback=20

    @staticmethod
    def _is_intraday_phase(input_phase:str|None)->bool:
        return str(input_phase or "").upper() in {"PREOPEN","OPEN","BREAK"}

    @classmethod
    def _completed_index(cls,panel:Any,input_phase:str|None)->int:
        i=len(panel.ts)-1
        if cls._is_intraday_phase(input_phase):
            i-=1
        return i

    @classmethod
    def _adv_notional(cls,panel:Any,symbol:str,i:int)->float:
        if i<0:
            return 0.0
        close=[float(x) for x in panel.close.get(symbol,[])]
        volume=[float(x) for x in panel.volume.get(symbol,[])]
        if not close or not volume:
            return 0.0
        lo=max(0,i+1-cls.adv_lookback)
        vals=[
            close[j]*volume[j]
            for j in range(lo,min(i+1,len(close),len(volume)))
            if close[j]>0 and volume[j]>0
        ]
        return mean(vals) if vals else 0.0

    @staticmethod
    def _execution_bps(
        participation:float,
        base_cost_bps:float,
        impact_coefficient_bps:float,
    )->dict[str,float]:
        p=max(0.0,float(participation))
        impact=float(impact_coefficient_bps)*math.sqrt(p)
        return {
            "base_cost_bps":float(base_cost_bps),
            "impact_bps":impact,
            "all_in_bps_per_side":float(base_cost_bps)+impact,
        }

    def build(
        self,
        panel:Any,
        trade_opinions:list[dict],
        input_phase:str|None,
    )->dict:
        market=str(panel.spec.market_id).upper()
        if market!="CN":
            return {
                "version":self.version,
                "experiment_version":self.experiment_version,
                "enabled":False,
                "reason":"CN_CNY_SLEEVES_ONLY",
            }

        completed_i=self._completed_index(panel,input_phase)
        params={
            "currency":"CNY",
            "adv_lookback_days":self.adv_lookback,
            "adv_source":"REAL_REPORTED_VOLUME_X_CLOSE_NOTIONAL_PROXY",
            "completed_liquidity_index":completed_i,
            "base_cost_bps":float(panel.spec.base_cost_bps),
            "impact_coefficient_bps":float(panel.spec.impact_coefficient_bps),
            "max_participation_adv":float(panel.spec.max_participation_adv),
            "impact_formula":"impact_bps = impact_coefficient_bps * sqrt(executed_notional / observed_ADV_notional)",
            "cost_formula":"cost = executed_notional * (base_cost_bps + impact_bps) / 10000",
            "parameter_provenance":"EXISTING_TRIAID_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_THIS_EXPERIMENT",
            "broker_specific_fees_included":False,
            "official_exchange_turnover_field_used":False,
        }

        adv_by_symbol={
            str(op.get("symbol")):self._adv_notional(panel,str(op.get("symbol")),completed_i)
            for op in trade_opinions
            if op.get("symbol")
        }

        rows=[]
        for capital in self.sleeves:
            products=[]
            gross_pnl=0.0
            one_way_cost=0.0
            round_trip_cost_proxy=0.0
            invested=0.0
            max_days=0
            max_one_day_participation=0.0
            missing_liquidity=False
            for op in trade_opinions:
                symbol=str(op.get("symbol"))
                weight=max(0.0,float(op.get("target_weight") or 0.0))
                target_notional=capital*weight
                invested+=target_notional
                adv=float(adv_by_symbol.get(symbol) or 0.0)
                one_day_participation=(target_notional/adv) if adv>0 and target_notional>0 else 0.0
                max_one_day_participation=max(max_one_day_participation,one_day_participation)
                daily_capacity=adv*params["max_participation_adv"] if adv>0 else 0.0
                min_days=(
                    int(math.ceil(target_notional/daily_capacity))
                    if target_notional>0 and daily_capacity>0 else
                    (0 if target_notional<=0 else None)
                )
                if isinstance(min_days,int):
                    max_days=max(max_days,min_days)
                elif target_notional>0:
                    missing_liquidity=True

                modeled_participation=(
                    min(one_day_participation,params["max_participation_adv"])
                    if one_day_participation>0 else 0.0
                )
                bps=self._execution_bps(
                    modeled_participation,
                    params["base_cost_bps"],
                    params["impact_coefficient_bps"],
                )
                entry_cost=target_notional*bps["all_in_bps_per_side"]/10000.0
                expected_return=op.get("expected_forward_return")
                expected_gross=(
                    target_notional*float(expected_return)
                    if expected_return is not None else 0.0
                )
                gross_pnl+=expected_gross
                one_way_cost+=entry_cost
                round_trip_cost_proxy+=2.0*entry_cost
                products.append({
                    "symbol":symbol,
                    "target_weight":weight,
                    "target_notional_cny":target_notional,
                    "adv20_notional_cny":adv,
                    "one_day_participation_adv":one_day_participation,
                    "max_participation_adv":params["max_participation_adv"],
                    "planned_participation_adv":modeled_participation,
                    "daily_capacity_notional_cny":daily_capacity,
                    "minimum_execution_days":min_days,
                    "all_in_bps_per_side":bps["all_in_bps_per_side"],
                    "estimated_entry_cost_cny":entry_cost,
                    "round_trip_cost_proxy_cny":2.0*entry_cost,
                    "expected_reversal_horizon_days":op.get("expected_reversal_horizon_days"),
                    "expected_forward_return":expected_return,
                    "expected_gross_wave_pnl_cny":expected_gross,
                })

            expected_net=gross_pnl-round_trip_cost_proxy
            if invested<=0:
                capacity_status="NO_RISK_POSITION"
            elif missing_liquidity:
                capacity_status="LIQUIDITY_DATA_UNAVAILABLE"
            elif max_days<=1:
                capacity_status="ONE_DAY_WITHIN_PARTICIPATION_CAP"
            else:
                capacity_status="MULTI_DAY_EXECUTION_REQUIRED"
            rows.append({
                "sleeve_id":f"CNY_{int(capital)}",
                "starting_capital_cny":capital,
                "starting_cash_only":True,
                "target_invested_notional_cny":invested,
                "target_cash_notional_cny":max(0.0,capital-invested),
                "target_risk_weight":invested/capital if capital>0 else 0.0,
                "max_one_day_participation_adv":max_one_day_participation,
                "minimum_execution_days":max_days,
                "capacity_status":capacity_status,
                "liquidity_data_complete":not missing_liquidity,
                "estimated_entry_cost_cny":one_way_cost,
                "estimated_round_trip_cost_proxy_cny":round_trip_cost_proxy,
                "expected_wave_gross_pnl_cny":gross_pnl,
                "expected_wave_net_pnl_before_timing_delay_cny":expected_net,
                "expected_wave_gross_return_on_capital":gross_pnl/capital if capital>0 else 0.0,
                "expected_wave_net_return_before_timing_delay":expected_net/capital if capital>0 else 0.0,
                "products":products,
            })

        return {
            "version":self.version,
            "experiment_version":self.experiment_version,
            "enabled":True,
            "experiment_design":"Four cash-start sleeves share one frozen signal and target weights; only starting capital differs.",
            "capital_sleeves_cny":[int(x) for x in self.sleeves],
            "execution_discipline":"FUTURE_REALIZED_FILLS_USE_OBSERVED_DAILY_TURNOVER_AND_BECOME_RETURN_ACTIVE_NEXT_COMPLETE_BAR",
            "fill_semantics":"SIMULATED_NOT_BROKER_FILLS; future observed real turnover constrains modeled fill capacity",
            "expected_model_scope":"HISTORICAL-ANALOGUE RETURN PROXY PLUS MODELED COST/CAPACITY; MULTI-DAY ENTRY TIMING DELAY IS NOT DEDUCTED FROM THE PRE-DECISION PROXY; POSTERIOR DAILY REVIEW IS SEPARATE",
            "model":params,
            "adv20_notional_by_symbol_cny":adv_by_symbol,
            "sleeves":rows,
        }

    @classmethod
    def realized_review(cls,decision:dict,outcomes:list[dict])->dict|None:
        capacity=decision.get("capital_capacity") or {}
        if not capacity.get("enabled"):
            return None
        model=capacity.get("model") or {}
        max_participation=float(model.get("max_participation_adv") or 0.0)
        base_cost=float(model.get("base_cost_bps") or 0.0)
        impact_coeff=float(model.get("impact_coefficient_bps") or 0.0)
        sleeves=capacity.get("sleeves") or []
        if not sleeves:
            return None

        result_sleeves=[]
        for frozen in sleeves:
            capital=float(frozen.get("starting_capital_cny") or 0.0)
            targets={
                str(p.get("symbol")):float(p.get("target_notional_cny") or 0.0)
                for p in (frozen.get("products") or [])
                if p.get("symbol")
            }
            remaining=deepcopy(targets)
            positions={s:0.0 for s in targets}
            cash=capital
            total_cost=0.0
            total_filled=0.0
            max_observed_participation=0.0
            completed_fill_date=None
            daily=[]
            previous_equity=capital
            incomplete_turnover_dates=[]

            for index,o in enumerate(outcomes,1):
                returns=o.get("product_returns") or {}
                turnover=o.get("product_turnover") or {}

                # Existing positions earn the full next complete bar return.
                for symbol in positions:
                    if symbol in returns:
                        positions[symbol]*=1.0+float(returns[symbol])

                fills={}
                day_cost=0.0
                for symbol in positions:
                    rem=max(0.0,float(remaining.get(symbol) or 0.0))
                    if rem<=1e-9:
                        fills[symbol]=0.0
                        continue
                    day_turnover=float(turnover.get(symbol) or 0.0)
                    if day_turnover<=0:
                        fills[symbol]=0.0
                        incomplete_turnover_dates.append(o.get("as_of"))
                        continue
                    cap=day_turnover*max_participation
                    planned=min(rem,cap)
                    if planned<=0:
                        fills[symbol]=0.0
                        continue
                    participation=planned/day_turnover
                    bps=cls._execution_bps(participation,base_cost,impact_coeff)
                    cost=planned*bps["all_in_bps_per_side"]/10000.0
                    affordable=max(0.0,cash-cost)
                    fill=min(planned,affordable)
                    if fill<=0:
                        fills[symbol]=0.0
                        continue
                    # Recompute cost after any cash-constrained resize.
                    participation=fill/day_turnover
                    bps=cls._execution_bps(participation,base_cost,impact_coeff)
                    cost=fill*bps["all_in_bps_per_side"]/10000.0
                    if fill+cost>cash:
                        fill=cash/(1.0+bps["all_in_bps_per_side"]/10000.0)
                        participation=fill/day_turnover
                        bps=cls._execution_bps(participation,base_cost,impact_coeff)
                        cost=fill*bps["all_in_bps_per_side"]/10000.0
                    positions[symbol]+=fill
                    cash-=fill+cost
                    remaining[symbol]=max(0.0,rem-fill)
                    fills[symbol]=fill
                    total_filled+=fill
                    total_cost+=cost
                    day_cost+=cost
                    max_observed_participation=max(max_observed_participation,participation)

                equity=cash+sum(positions.values())
                day_return=(equity/previous_equity-1.0) if previous_equity>0 else 0.0
                previous_equity=equity
                if completed_fill_date is None and all(v<=1e-6 for v in remaining.values()):
                    completed_fill_date=o.get("as_of")
                daily.append({
                    "period_number":index,
                    "as_of":o.get("as_of"),
                    "positions_cny":deepcopy(positions),
                    "cash_cny":cash,
                    "fills_cny":fills,
                    "execution_cost_cny":day_cost,
                    "equity_cny":equity,
                    "daily_net_return":day_return,
                    "cumulative_net_return":equity/capital-1.0 if capital>0 else 0.0,
                    "cumulative_net_pnl_cny":equity-capital,
                    "remaining_target_notional_cny":sum(remaining.values()),
                })

            target_total=sum(targets.values())
            result_sleeves.append({
                "sleeve_id":frozen.get("sleeve_id"),
                "starting_capital_cny":capital,
                "target_notional_cny":target_total,
                "filled_notional_cny":total_filled,
                "fill_ratio":(total_filled/target_total) if target_total>0 else 1.0,
                "remaining_target_notional_cny":sum(remaining.values()),
                "total_execution_cost_cny":total_cost,
                "max_observed_participation_adv":max_observed_participation,
                "full_fill_date":completed_fill_date,
                "current_equity_cny":cash+sum(positions.values()),
                "current_net_pnl_cny":cash+sum(positions.values())-capital,
                "current_net_return":(cash+sum(positions.values()))/capital-1.0 if capital>0 else 0.0,
                "observation_days":len(outcomes),
                "incomplete_turnover_dates":sorted(set(x for x in incomplete_turnover_dates if x)),
                "daily_path":daily,
            })

        return {
            "version":cls.version,
            "experiment_version":cls.experiment_version,
            "review_discipline":"FILLS_USE_ONLY_FUTURE_OBSERVED_TURNOVER; FILLS_BECOME RETURN-ACTIVE ON THE FOLLOWING COMPLETE BAR",
            "fill_semantics":"SIMULATED_NOT_BROKER_FILLS",
            "sleeves":result_sleeves,
        }

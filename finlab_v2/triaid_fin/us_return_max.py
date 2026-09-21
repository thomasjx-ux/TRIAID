from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from math import prod
from statistics import mean
from typing import Any

from .contracts import StrategyGroup, StrategyState, TriaidDecision, utc_now
from .market_lab import policy_positions
from .store import RunStore


USD_CAPITAL_SLEEVES=(100_000.0,1_000_000.0,10_000_000.0,100_000_000.0)


class USReturnMaxRoute:
    """US return-maximization route with executable capital-capacity sleeves.

    The route intentionally differs from the CN recovery-wave route:
    - primary selection is the maximum current multi-window annualized historical state-return estimate across admissible active strategies;
    - ties are broken deterministically by lower estimated cost, risk, uncertainty, then ID;
    - no recovery/drawdown thesis is required;
    - the frozen winner is expanded to executable ETF exposures;
    - four USD sleeves share the same signal and differ only by starting capital.
    """

    version="us-return-max-route@0.2.0"
    interface_version="us-return-max-contract@1"
    capital_version="us-return-max-capacity@0.1.0"
    sleeves=USD_CAPITAL_SLEEVES
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
    def _execution_bps(participation:float,base_cost_bps:float,impact_coefficient_bps:float)->float:
        return float(base_cost_bps)+float(impact_coefficient_bps)*math.sqrt(max(0.0,float(participation)))

    @staticmethod
    def _weighted_expected(weights:dict[str,float],state_map:dict[str,StrategyState])->float:
        return sum(
            float(w)*(0.0 if sid=="P28_CASH" else float(state_map[sid].expected_net_return))
            for sid,w in weights.items()
            if sid=="P28_CASH" or sid in state_map
        )

    @staticmethod
    def _strict_max_strategy(states:list[StrategyState])->tuple[StrategyState,list[str]]:
        eligible=[
            s for s in states
            if str(s.lifecycle or "").lower()=="active"
            and bool(s.eligible)
            and not bool(s.hard_failure)
            and bool(s.liquidity_ok)
            and bool(s.capacity_ok)
            and bool(s.risk_ok)
            and bool(s.concentration_ok)
        ]
        if not eligible:
            raise ValueError("US Return-Max requires at least one active strategy.")
        def expected(s:StrategyState)->float:
            return 0.0 if s.strategy_id=="P28_CASH" else float(s.expected_net_return)
        best=max(expected(s) for s in eligible)
        tied=[
            s for s in eligible
            if abs(expected(s)-best)<=1e-12
        ]
        winner=min(
            tied,
            key=lambda s:(
                float(s.estimated_cost or 0.0),
                float(s.risk or 0.0),
                float(s.uncertainty or 0.0),
                str(s.strategy_id),
            ),
        )
        return winner,sorted(str(s.strategy_id) for s in tied)

    @staticmethod
    def _asset_targets(panel:Any,i:int,strategy_weights:dict[str,float])->dict[str,float]:
        positions=policy_positions(panel,i)
        assets=panel.assets
        out={a:0.0 for a in assets}
        for sid,sw in strategy_weights.items():
            if sid=="P28_CASH" or float(sw)<=0:
                continue
            vec=positions.get(sid)
            if vec is None:
                continue
            for asset,w in zip(assets,vec):
                out[asset]+=float(sw)*max(0.0,float(w))
        return {a:max(0.0,float(w)) for a,w in out.items()}

    def decide(
        self,
        panel:Any,
        group:StrategyGroup,
        generic_decision:TriaidDecision,
        states:list[StrategyState],
        input_phase:str|None,
    )->dict:
        if str(panel.spec.market_id).upper()!="US":
            raise ValueError("USReturnMaxRoute only supports US.")

        visible_i=len(panel.ts)-1
        completed_i=self._completed_index(panel,input_phase)
        state_map={s.strategy_id:s for s in states}

        winner,tie_set=self._strict_max_strategy(states)
        route_weights={str(winner.strategy_id):1.0}
        population_weights={str(k):float(v) for k,v in group.weights.items()}
        generic_weights={str(k):float(v) for k,v in generic_decision.weights_after.items()}
        route_expected=self._weighted_expected(route_weights,state_map)
        population_expected=self._weighted_expected(population_weights,state_map)
        generic_expected=self._weighted_expected(generic_weights,state_map)
        buy_hold_expected=(
            float(state_map["P00_BUY_HOLD"].expected_net_return)
            if "P00_BUY_HOLD" in state_map else None
        )

        target_assets=self._asset_targets(panel,visible_i,route_weights)
        target_risk_weight=sum(target_assets.values())
        adv_by_symbol={
            a:self._adv_notional(panel,a,completed_i)
            for a in target_assets
        }
        spec=panel.spec

        sleeves=[]
        for capital in self.sleeves:
            products=[]
            total_entry_cost=0.0
            target_notional=0.0
            max_participation=0.0
            max_days=0
            missing_liquidity=False
            for asset,w in target_assets.items():
                notional=capital*float(w)
                target_notional+=notional
                adv=float(adv_by_symbol.get(asset) or 0.0)
                one_day_participation=notional/adv if notional>0 and adv>0 else 0.0
                max_participation=max(max_participation,one_day_participation)
                daily_capacity=adv*float(spec.max_participation_adv) if adv>0 else 0.0
                min_days=(
                    int(math.ceil(notional/daily_capacity))
                    if notional>0 and daily_capacity>0 else
                    (0 if notional<=0 else None)
                )
                if isinstance(min_days,int):
                    max_days=max(max_days,min_days)
                elif notional>0:
                    missing_liquidity=True
                planned_participation=min(one_day_participation,float(spec.max_participation_adv)) if one_day_participation>0 else 0.0
                bps=self._execution_bps(
                    planned_participation,
                    float(spec.base_cost_bps),
                    float(spec.impact_coefficient_bps),
                )
                entry_cost=notional*bps/10000.0
                total_entry_cost+=entry_cost
                products.append({
                    "symbol":asset,
                    "target_weight":float(w),
                    "target_notional_usd":notional,
                    "adv20_notional_usd":adv,
                    "one_day_participation_adv":one_day_participation,
                    "planned_participation_adv":planned_participation,
                    "daily_capacity_notional_usd":daily_capacity,
                    "minimum_execution_days":min_days,
                    "all_in_bps_per_side":bps,
                    "estimated_entry_cost_usd":entry_cost,
                })
            if target_notional<=0:
                capacity_status="NO_RISK_POSITION"
            elif missing_liquidity:
                capacity_status="LIQUIDITY_DATA_UNAVAILABLE"
            elif max_days<=1:
                capacity_status="ONE_DAY_WITHIN_PARTICIPATION_CAP"
            else:
                capacity_status="MULTI_DAY_EXECUTION_REQUIRED"
            sleeves.append({
                "sleeve_id":f"USD_{int(capital)}",
                "starting_capital_usd":capital,
                "starting_cash_only":True,
                "target_invested_notional_usd":target_notional,
                "target_cash_notional_usd":max(0.0,capital-target_notional),
                "target_risk_weight":target_notional/capital if capital>0 else 0.0,
                "max_one_day_participation_adv":max_participation,
                "minimum_execution_days":max_days,
                "capacity_status":capacity_status,
                "liquidity_data_complete":not missing_liquidity,
                "estimated_entry_cost_usd":total_entry_cost,
                "estimated_round_trip_cost_proxy_usd":2.0*total_entry_cost,
                "products":products,
            })

        return {
            "route_version":self.version,
            "interface_version":self.interface_version,
            "capital_version":self.capital_version,
            "market_id":"US",
            "source_latest_ts":panel.ts[-1],
            "input_phase":input_phase,
            "decision_status":"PROVISIONAL_INTRADAY" if self._is_intraday_phase(input_phase) else "DAILY_FROZEN",
            "research_only":True,
            "broker_execution_enabled":False,
            "objective":"STRICT_MAXIMIZE_CURRENT_MULTI_WINDOW_STATE_RETURN_ESTIMATE_ACROSS_ADMISSIBLE_ACTIVE_STRATEGIES_THEN_APPLY_EXECUTION_CAPACITY",
            "selection_source":"ALL_ADMISSIBLE_ACTIVE_STRATEGIES_STRICT_MAX_STATE_RETURN_ESTIMATE",
            "strategy_selection_mode":"STRICT_MAX_STATE_RETURN_ESTIMATE_WITH_DETERMINISTIC_TIE_BREAK",
            "selected_strategy_id":str(winner.strategy_id),
            "max_return_tie_set":tie_set,
            "tie_break_order":["estimated_cost","risk","uncertainty","strategy_id"],
            "target_strategy_weights":route_weights,
            "return_first_population_control_weights":population_weights,
            "generic_core_control_weights":generic_weights,
            "projected_annualized_expected_net_return":route_expected,
            "return_first_population_projected_annualized_expected_net_return":population_expected,
            "generic_core_projected_annualized_expected_net_return":generic_expected,
            "buy_hold_projected_annualized_expected_net_return":buy_hold_expected,
            "selection_metric_semantics":"StrategyState.expected_net_return is a legacy field name for the weighted 21/63/126/252-day annualized historical strategy state-return estimate; it is not a calibrated future-return forecast.",
            "projected_field_semantics":"Fields named projected_annualized_expected_net_return preserve the existing API contract but contain weighted state-return estimates under the frozen decision, not guaranteed or calibrated future returns.",
            "target_asset_weights":target_assets,
            "cash_residual_weight":max(0.0,1.0-target_risk_weight),
            "controls":{
                "SPY_BUY_HOLD":"P00_BUY_HOLD / SPY",
                "RETURN_FIRST_POPULATION":"Existing multi-strategy return-first population mix frozen at the same decision time",
                "GENERIC_TRIAID_CORE":"Existing generic TRIAID Core allocation frozen at the same decision time",
            },
            "execution_discipline":{
                "same_bar_execution_allowed":False,
                "execution_rule":"DECISION_AT_T_APPLIES_FROM_NEXT_COMPLETE_TRADABLE_BAR",
                "future_fill_rule":"FUTURE_REALIZED_FILLS_USE_OBSERVED_DAILY_TURNOVER_AND_BECOME_RETURN_ACTIVE_NEXT_COMPLETE_BAR",
            },
            "capital_capacity":{
                "version":self.capital_version,
                "currency":"USD",
                "capital_sleeves_usd":[int(x) for x in self.sleeves],
                "adv_lookback_days":self.adv_lookback,
                "adv_source":"REAL_REPORTED_VOLUME_X_CLOSE_NOTIONAL_PROXY",
                "completed_liquidity_index":completed_i,
                "base_cost_bps":float(spec.base_cost_bps),
                "impact_coefficient_bps":float(spec.impact_coefficient_bps),
                "max_participation_adv":float(spec.max_participation_adv),
                "impact_formula":"impact_bps = impact_coefficient_bps * sqrt(executed_notional / observed_ADV_notional)",
                "parameter_provenance":"EXISTING_US_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_THIS_EXPERIMENT",
                "broker_specific_fees_included":False,
                "sleeves":sleeves,
            },
        }


class USReturnMaxLedger:
    version="us-return-max-ledger@0.1.0"
    decision_file="us_return_max_decisions.jsonl"
    outcome_file="us_return_max_outcomes.jsonl"
    index_file="us_return_max_index.json"
    outcome_index_file="us_return_max_outcome_index.json"

    def __init__(self,store:RunStore)->None:
        self.store=store
        self.index=store.load_json(self.index_file,default={}) or {}
        self.outcome_index=store.load_json(self.outcome_index_file,default={}) or {}

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    def decisions(self,limit:int=1000)->list[dict]:
        return self.store.read_jsonl(self.decision_file,limit=limit)

    def outcomes(self,limit:int=2000)->list[dict]:
        return self.store.read_jsonl(self.outcome_file,limit=limit)

    def latest(self)->dict|None:
        rows=self.decisions(1000)
        return deepcopy(rows[-1]) if rows else None

    def by_snapshot(self,snapshot_id:str,route_version:str|None=None)->dict|None:
        key=f"{snapshot_id}:{route_version}" if route_version else snapshot_id
        decision_id=(self.index.get("snapshots") or {}).get(key)
        rows=self.decisions(5000)
        if decision_id:
            for row in reversed(rows):
                if row.get("decision_id")==decision_id:
                    return deepcopy(row)
        if route_version:
            for row in reversed(rows):
                if row.get("snapshot_id")==snapshot_id and row.get("route_version")==route_version:
                    return deepcopy(row)
        return None

    def freeze(self,decision:dict,snapshot_id:str,market_as_of:str)->dict:
        route_version=str(decision.get("route_version") or "unknown")
        existing=self.by_snapshot(snapshot_id,route_version)
        if existing:
            return existing
        prior=self.latest()
        row=deepcopy(decision)
        row["snapshot_id"]=snapshot_id
        row["market_as_of"]=market_as_of
        row["frozen_at"]=utc_now()
        row["previous_decision_id"]=prior.get("decision_id") if prior else None
        row["previous_decision_hash"]=prior.get("decision_hash") if prior else None
        body=deepcopy(row)
        row["decision_hash"]=self._hash(body)
        row["decision_id"]=f"USRM-{row.get('source_latest_ts')}-{row['decision_hash'][:10]}"
        self.store.append_jsonl(self.decision_file,row)
        snapshots=self.index.setdefault("snapshots",{})
        snapshots[f"{snapshot_id}:{route_version}"]=row["decision_id"]
        snapshots[snapshot_id]=row["decision_id"]
        self.index["latest_decision_id"]=row["decision_id"]
        self.index["latest_decision_hash"]=row["decision_hash"]
        self.store.save_json(self.index_file,self.index)
        return deepcopy(row)

    def record_outcome(
        self,
        as_of:str,
        period_start_as_of:str,
        strategy_returns:dict[str,float],
        product_returns:dict[str,float],
        product_turnover:dict[str,float],
        source_snapshot_id:str|None=None,
    )->dict:
        key=f"US:{as_of}"
        if key in self.outcome_index:
            return {"recorded":False,"reason":"DUPLICATE_OUTCOME_DATE","outcome_id":self.outcome_index[key]}
        row={
            "outcome_id":f"USRMO-{as_of}",
            "recorded_at":utc_now(),
            "market_id":"US",
            "as_of":as_of,
            "period_start_as_of":period_start_as_of,
            "source_snapshot_id":source_snapshot_id,
            "strategy_returns":{str(k):float(v) for k,v in strategy_returns.items()},
            "product_returns":{str(k):float(v) for k,v in product_returns.items()},
            "product_turnover":{str(k):float(v) for k,v in product_turnover.items() if float(v)>0},
        }
        self.store.append_jsonl(self.outcome_file,row)
        self.outcome_index[key]=row["outcome_id"]
        self.store.save_json(self.outcome_index_file,self.outcome_index)
        return {"recorded":True,"outcome":deepcopy(row)}

    @staticmethod
    def _compound(xs:list[float])->float:
        return prod(1.0+float(x) for x in xs)-1.0 if xs else 0.0

    @staticmethod
    def _weighted_return(weights:dict[str,float],returns:dict[str,float])->float:
        return sum(float(w)*float(returns.get(k,0.0)) for k,w in weights.items())

    @staticmethod
    def _execution_bps(participation:float,base_cost_bps:float,impact_coefficient_bps:float)->float:
        return float(base_cost_bps)+float(impact_coefficient_bps)*math.sqrt(max(0.0,float(participation)))

    def _review_sleeves(self,decision:dict,future:list[dict])->dict:
        cap=decision.get("capital_capacity") or {}
        max_part=float(cap.get("max_participation_adv") or 0.0)
        base_cost=float(cap.get("base_cost_bps") or 0.0)
        impact=float(cap.get("impact_coefficient_bps") or 0.0)
        out=[]
        for frozen in cap.get("sleeves") or []:
            capital=float(frozen.get("starting_capital_usd") or 0.0)
            targets={
                str(p.get("symbol")):float(p.get("target_notional_usd") or 0.0)
                for p in frozen.get("products") or []
                if p.get("symbol")
            }
            remaining=deepcopy(targets)
            positions={s:0.0 for s in targets}
            cash=capital
            total_cost=0.0
            total_filled=0.0
            prior_equity=capital
            daily=[]
            full_fill_date=None
            incomplete_turnover_dates=[]
            for index,row in enumerate(future,1):
                returns=row.get("product_returns") or {}
                turnover=row.get("product_turnover") or {}
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
                        incomplete_turnover_dates.append(row.get("as_of"))
                        continue
                    planned=min(rem,day_turnover*max_part)
                    if planned<=0:
                        fills[symbol]=0.0
                        continue
                    participation=planned/day_turnover
                    bps=self._execution_bps(participation,base_cost,impact)
                    affordable=max(0.0,cash)/(1.0+bps/10000.0)
                    fill=min(planned,affordable)
                    if fill<=0:
                        fills[symbol]=0.0
                        continue
                    participation=fill/day_turnover
                    bps=self._execution_bps(participation,base_cost,impact)
                    cost=fill*bps/10000.0
                    positions[symbol]+=fill
                    cash-=fill+cost
                    remaining[symbol]=max(0.0,rem-fill)
                    total_filled+=fill
                    total_cost+=cost
                    day_cost+=cost
                    fills[symbol]=fill
                equity=cash+sum(positions.values())
                day_return=equity/prior_equity-1.0 if prior_equity>0 else 0.0
                prior_equity=equity
                if full_fill_date is None and all(v<=1e-6 for v in remaining.values()):
                    full_fill_date=row.get("as_of")
                daily.append({
                    "period_number":index,
                    "as_of":row.get("as_of"),
                    "fills_usd":fills,
                    "execution_cost_usd":day_cost,
                    "cash_usd":cash,
                    "positions_usd":deepcopy(positions),
                    "equity_usd":equity,
                    "daily_net_return":day_return,
                    "cumulative_net_return":equity/capital-1.0 if capital>0 else 0.0,
                    "cumulative_net_pnl_usd":equity-capital,
                    "remaining_target_notional_usd":sum(remaining.values()),
                })
            target_total=sum(targets.values())
            equity=cash+sum(positions.values())
            out.append({
                "sleeve_id":frozen.get("sleeve_id"),
                "starting_capital_usd":capital,
                "target_notional_usd":target_total,
                "filled_notional_usd":total_filled,
                "fill_ratio":total_filled/target_total if target_total>0 else 1.0,
                "remaining_target_notional_usd":sum(remaining.values()),
                "total_execution_cost_usd":total_cost,
                "full_fill_date":full_fill_date,
                "current_equity_usd":equity,
                "current_net_pnl_usd":equity-capital,
                "current_net_return":equity/capital-1.0 if capital>0 else 0.0,
                "observation_days":len(future),
                "incomplete_turnover_dates":sorted(set(x for x in incomplete_turnover_dates if x)),
                "daily_path":daily,
            })
        return {
            "version":decision.get("capital_version"),
            "review_discipline":"FILLS_USE_ONLY_FUTURE_OBSERVED_TURNOVER; FILLS_BECOME RETURN-ACTIVE ON THE FOLLOWING COMPLETE BAR",
            "sleeves":out,
        }

    def review_decision(self,decision:dict)->dict:
        decision_date=str(decision.get("market_as_of") or "")
        future=[o for o in self.outcomes(4000) if str(o.get("as_of") or "")>decision_date]
        route_weights={str(k):float(v) for k,v in (decision.get("target_strategy_weights") or {}).items()}
        generic_weights={str(k):float(v) for k,v in (decision.get("generic_core_control_weights") or {}).items()}
        daily=[]
        route_daily=[]
        generic_daily=[]
        spy_daily=[]
        for index,row in enumerate(future,1):
            sr=row.get("strategy_returns") or {}
            pr=row.get("product_returns") or {}
            route_r=self._weighted_return(route_weights,sr)
            generic_r=self._weighted_return(generic_weights,sr)
            spy_r=float(pr.get("SPY",0.0))
            route_daily.append(route_r)
            generic_daily.append(generic_r)
            spy_daily.append(spy_r)
            daily.append({
                "period_number":index,
                "as_of":row.get("as_of"),
                "return_max_theoretical_return":route_r,
                "generic_core_theoretical_return":generic_r,
                "spy_buy_hold_return":spy_r,
                "return_max_cumulative_return":self._compound(route_daily),
                "generic_core_cumulative_return":self._compound(generic_daily),
                "spy_buy_hold_cumulative_return":self._compound(spy_daily),
            })
        horizons={}
        for h in (1,3,5):
            if len(daily)>=h:
                x=daily[h-1]
                horizons[str(h)]={
                    "horizon_days":h,
                    "return_max_cumulative_return":x["return_max_cumulative_return"],
                    "generic_core_cumulative_return":x["generic_core_cumulative_return"],
                    "spy_buy_hold_cumulative_return":x["spy_buy_hold_cumulative_return"],
                }
        return {
            "decision_id":decision.get("decision_id"),
            "decision_hash":decision.get("decision_hash"),
            "market_as_of":decision_date,
            "frozen_at":decision.get("frozen_at"),
            "observation_days":len(future),
            "daily_path":daily,
            "current_return_max_theoretical_return":self._compound(route_daily),
            "current_generic_core_theoretical_return":self._compound(generic_daily),
            "current_spy_buy_hold_return":self._compound(spy_daily),
            "matured_horizons":horizons,
            "capital_sleeves":self._review_sleeves(decision,future),
        }

    def verify_integrity(self)->dict:
        rows=self.decisions(5000)
        previous_hash=None
        checks=[]
        for row in rows:
            body={k:deepcopy(v) for k,v in row.items() if k not in {"decision_hash","decision_id"}}
            expected=self._hash(body)
            hash_ok=expected==row.get("decision_hash")
            chain_ok=row.get("previous_decision_hash")==previous_hash
            checks.append({"decision_id":row.get("decision_id"),"hash_ok":hash_ok,"chain_ok":chain_ok})
            previous_hash=row.get("decision_hash")
        return {
            "passed":all(x["hash_ok"] and x["chain_ok"] for x in checks),
            "decision_count":len(rows),
            "checks":checks[-20:],
        }

    def daily_report(self)->dict|None:
        rows=self.decisions(1000)
        if not rows:
            return None
        latest=rows[-1]
        previous=rows[-2] if len(rows)>1 else None
        return {
            "report_version":self.version,
            "route_version":latest.get("route_version"),
            "latest_decision":deepcopy(latest),
            "latest_decision_review":self.review_decision(latest),
            "previous_decision_review":self.review_decision(previous) if previous else None,
            "latest_outcome_as_of":self.outcomes(1)[-1].get("as_of") if self.outcomes(1) else None,
            "integrity":self.verify_integrity(),
            "interpretation_guard":"US Return-Max uses a frozen ranking signal based on the multi-window annualized historical state-return estimate. Four USD sleeves differ only by capital size. Theoretical-holdings posterior returns and simulated-execution sleeve returns are reported separately; modeled impact and fills are not broker executions.",
        }

    def status(self)->dict:
        return {
            "version":self.version,
            "decision_count":len(self.decisions(5000)),
            "outcome_count":len(self.outcomes(5000)),
            "integrity":self.verify_integrity(),
        }

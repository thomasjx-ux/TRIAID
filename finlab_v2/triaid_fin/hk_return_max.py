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
from .objective import OBJECTIVE_CONSTITUTION, PRIMARY_OBJECTIVE
from .store import RunStore


HKD_CAPITAL_SLEEVES=(100_000.0,1_000_000.0,10_000_000.0,100_000_000.0)


class HKReturnMaxRoute:
    """HK return-max route with HKD capacity sleeves.

    The generic strategy population and TRIAID Core remain the decision source.
    This module closes the HK market-specific realizability layer by freezing:
    strategy weights, ETF exposures, execution/capacity assumptions, and the
    four capital sleeves that are later reviewed only with future observations.
    """

    version="hk-return-max-route@0.2.0"
    interface_version="hk-return-max-contract@1"
    capital_version="hk-return-max-capacity@0.1.0"
    sleeves=HKD_CAPITAL_SLEEVES
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
    def _asset_targets(panel:Any,i:int,strategy_weights:dict[str,float])->dict[str,float]:
        positions=policy_positions(panel,i)
        out={a:0.0 for a in panel.assets}
        for sid,sw in strategy_weights.items():
            if sid=="P28_CASH" or float(sw)<=0:
                continue
            vec=positions.get(sid)
            if vec is None:
                continue
            for asset,w in zip(panel.assets,vec):
                out[asset]+=float(sw)*max(0.0,float(w))
        return {a:max(0.0,float(w)) for a,w in out.items()}

    def decide(
        self,
        panel:Any,
        group:StrategyGroup,
        triaid_decision:TriaidDecision,
        states:list[StrategyState],
        input_phase:str|None,
    )->dict:
        if str(panel.spec.market_id).upper()!="HK":
            raise ValueError("HKReturnMaxRoute only supports HK.")

        visible_i=len(panel.ts)-1
        completed_i=self._completed_index(panel,input_phase)
        spec=panel.spec
        state_map={s.strategy_id:s for s in states}
        baseline_weights={str(k):float(v) for k,v in group.weights.items()}
        target_weights={str(k):float(v) for k,v in triaid_decision.weights_after.items()}
        target_assets=self._asset_targets(panel,visible_i,target_weights)
        target_risk_weight=sum(target_assets.values())
        baseline_expected=self._weighted_expected(baseline_weights,state_map)
        target_expected=self._weighted_expected(target_weights,state_map)
        buy_hold_expected=(
            float(state_map["P00_BUY_HOLD"].expected_net_return)
            if "P00_BUY_HOLD" in state_map else None
        )
        adv_by_symbol={
            a:self._adv_notional(panel,a,completed_i)
            for a in target_assets
        }

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
                planned=min(one_day_participation,float(spec.max_participation_adv)) if one_day_participation>0 else 0.0
                bps=self._execution_bps(
                    planned,
                    float(spec.base_cost_bps),
                    float(spec.impact_coefficient_bps),
                )
                entry_cost=notional*bps/10000.0
                total_entry_cost+=entry_cost
                products.append({
                    "symbol":asset,
                    "target_weight":float(w),
                    "target_notional_hkd":notional,
                    "adv20_notional_hkd":adv,
                    "one_day_participation_adv":one_day_participation,
                    "planned_participation_adv":planned,
                    "daily_capacity_notional_hkd":daily_capacity,
                    "minimum_execution_days":min_days,
                    "all_in_bps_per_side":bps,
                    "estimated_entry_cost_hkd":entry_cost,
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
                "sleeve_id":f"HKD_{int(capital)}",
                "starting_capital_hkd":capital,
                "starting_cash_only":True,
                "target_invested_notional_hkd":target_notional,
                "target_cash_notional_hkd":max(0.0,capital-target_notional),
                "target_risk_weight":target_notional/capital if capital>0 else 0.0,
                "max_one_day_participation_adv":max_participation,
                "minimum_execution_days":max_days,
                "capacity_status":capacity_status,
                "liquidity_data_complete":not missing_liquidity,
                "estimated_entry_cost_hkd":total_entry_cost,
                "estimated_round_trip_cost_proxy_hkd":2.0*total_entry_cost,
                "products":products,
            })

        return {
            "route_version":self.version,
            "interface_version":self.interface_version,
            "capital_version":self.capital_version,
            "market_id":"HK",
            "source_latest_ts":panel.ts[-1],
            "input_phase":input_phase,
            "decision_status":"PROVISIONAL_INTRADAY" if self._is_intraday_phase(input_phase) else "DAILY_FROZEN",
            "research_only":True,
            "broker_execution_enabled":False,
            "objective":PRIMARY_OBJECTIVE,
            "objective_constitution":OBJECTIVE_CONSTITUTION,
            "selection_source":"HK_RETURN_FIRST_POPULATION_PLUS_TRIAID_CORE",
            "strategy_selection_mode":"MAX_REALIZABLE_NET_RETURN_WITH_HK_MARKET_CONSTRAINTS",
            "target_strategy_weights":target_weights,
            "baseline_strategy_weights":baseline_weights,
            "selected_strategy_ids":[sid for sid,w in target_weights.items() if sid!="P28_CASH" and float(w)>1e-12],
            "selected_strategy_count":sum(1 for sid,w in target_weights.items() if sid!="P28_CASH" and float(w)>1e-12),
            "projected_annualized_expected_net_return":target_expected,
            "baseline_projected_annualized_expected_net_return":baseline_expected,
            "buy_hold_projected_annualized_expected_net_return":buy_hold_expected,
            "selection_metric_semantics":"HK uses the same frozen multi-window state-return evidence and TRIAID Core discipline as the common strategy layer. These are historical/model state-return estimates, not calibrated future-return forecasts.",
            "target_asset_weights":target_assets,
            "cash_residual_weight":max(0.0,1.0-target_risk_weight),
            "controls":{
                "HK_RETURN_FIRST_BASELINE":"HK strategy-population mix frozen at the same decision time",
                "2800_HK_BUY_HOLD":"P00_BUY_HOLD / 2800.HK",
            },
            "execution_discipline":{
                "same_bar_execution_allowed":False,
                "execution_rule":"DECISION_AT_T_APPLIES_FROM_NEXT_COMPLETE_TRADABLE_BAR",
                "future_fill_rule":"FUTURE_REALIZED_FILLS_USE_OBSERVED_DAILY_TURNOVER_AND_BECOME_RETURN_ACTIVE_NEXT_COMPLETE_BAR",
            },
            "capital_capacity":{
                "version":self.capital_version,
                "currency":"HKD",
                "capital_sleeves_hkd":[int(x) for x in self.sleeves],
                "adv_lookback_days":self.adv_lookback,
                "adv_source":"REAL_REPORTED_VOLUME_X_CLOSE_NOTIONAL_PROXY",
                "completed_liquidity_index":completed_i,
                "base_cost_bps":float(spec.base_cost_bps),
                "impact_coefficient_bps":float(spec.impact_coefficient_bps),
                "max_participation_adv":float(spec.max_participation_adv),
                "impact_formula":"impact_bps = impact_coefficient_bps * sqrt(executed_notional / observed_ADV_notional)",
                "parameter_provenance":"EXISTING_HK_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_THIS_EXPERIMENT",
                "broker_specific_fees_included":False,
                "sleeves":sleeves,
            },
        }


class HKReturnMaxLedger:
    version="hk-return-max-ledger@0.2.0"
    decision_file="hk_return_max_decisions.jsonl"
    outcome_file="hk_return_max_outcomes.jsonl"
    index_file="hk_return_max_index.json"
    outcome_index_file="hk_return_max_outcome_index.json"

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
        row["decision_id"]=f"HKRM-{row.get('source_latest_ts')}-{row['decision_hash'][:10]}"
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
        key=f"HK:{as_of}"
        if key in self.outcome_index:
            return {"recorded":False,"reason":"DUPLICATE_OUTCOME_DATE","outcome_id":self.outcome_index[key]}
        for existing in reversed(self.outcomes(5000)):
            if str(existing.get("as_of") or "")==str(as_of):
                outcome_id=str(existing.get("outcome_id") or f"HKRMO-{as_of}")
                self.outcome_index[key]=outcome_id
                self.store.save_json(self.outcome_index_file,self.outcome_index)
                return {"recorded":False,"reason":"DUPLICATE_OUTCOME_DATE_RECOVERED_FROM_LEDGER","outcome_id":outcome_id}
        row={
            "outcome_id":f"HKRMO-{as_of}",
            "recorded_at":utc_now(),
            "market_id":"HK",
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
            capital=float(frozen.get("starting_capital_hkd") or 0.0)
            targets={
                str(p.get("symbol")):float(p.get("target_notional_hkd") or 0.0)
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
            incomplete_return_dates=[]
            required_products={s for s,v in targets.items() if float(v)>1e-9}
            sleeve_future=[]
            for row in future:
                returns=row.get("product_returns") or {}
                if any(s not in returns for s in required_products):
                    incomplete_return_dates.append(row.get("as_of"))
                    continue
                sleeve_future.append(row)
            for index,row in enumerate(sleeve_future,1):
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
                    "fills_hkd":fills,
                    "execution_cost_hkd":day_cost,
                    "cash_hkd":cash,
                    "positions_hkd":deepcopy(positions),
                    "equity_hkd":equity,
                    "daily_net_return":day_return,
                    "cumulative_net_return":equity/capital-1.0 if capital>0 else 0.0,
                    "cumulative_net_pnl_hkd":equity-capital,
                    "remaining_target_notional_hkd":sum(remaining.values()),
                })
            target_total=sum(targets.values())
            equity=cash+sum(positions.values())
            out.append({
                "sleeve_id":frozen.get("sleeve_id"),
                "starting_capital_hkd":capital,
                "target_notional_hkd":target_total,
                "filled_notional_hkd":total_filled,
                "fill_ratio":total_filled/target_total if target_total>0 else 1.0,
                "remaining_target_notional_hkd":sum(remaining.values()),
                "total_execution_cost_hkd":total_cost,
                "full_fill_date":full_fill_date,
                "current_equity_hkd":equity,
                "current_net_pnl_hkd":equity-capital,
                "current_net_return":equity/capital-1.0 if capital>0 else 0.0,
                "observation_days":len(sleeve_future),
                "incomplete_return_dates":sorted(set(x for x in incomplete_return_dates if x)),
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
        raw_future=[o for o in self.outcomes(4000) if str(o.get("as_of") or "")>decision_date]
        target_weights={str(k):float(v) for k,v in (decision.get("target_strategy_weights") or {}).items()}
        baseline_weights={str(k):float(v) for k,v in (decision.get("baseline_strategy_weights") or {}).items()}
        required_strategies={
            sid for sid,w in {**target_weights,**baseline_weights}.items()
            if sid!="P28_CASH" and float(w)>1e-12
        }
        incomplete_outcome_dates=[
            o.get("as_of") for o in raw_future
            if any(s not in (o.get("strategy_returns") or {}) for s in required_strategies)
            or "2800.HK" not in (o.get("product_returns") or {})
        ]
        future=[
            o for o in raw_future
            if all(s in (o.get("strategy_returns") or {}) for s in required_strategies)
            and "2800.HK" in (o.get("product_returns") or {})
        ]
        daily=[]
        target_daily=[]
        baseline_daily=[]
        benchmark_daily=[]
        for index,row in enumerate(future,1):
            sr=row.get("strategy_returns") or {}
            pr=row.get("product_returns") or {}
            target_r=self._weighted_return(target_weights,sr)
            baseline_r=self._weighted_return(baseline_weights,sr)
            benchmark_r=float(pr.get("2800.HK",0.0))
            target_daily.append(target_r)
            baseline_daily.append(baseline_r)
            benchmark_daily.append(benchmark_r)
            daily.append({
                "period_number":index,
                "as_of":row.get("as_of"),
                "triaid_theoretical_return":target_r,
                "baseline_theoretical_return":baseline_r,
                "benchmark_buy_hold_return":benchmark_r,
                "triaid_cumulative_return":self._compound(target_daily),
                "baseline_cumulative_return":self._compound(baseline_daily),
                "benchmark_cumulative_return":self._compound(benchmark_daily),
            })
        return {
            "decision_id":decision.get("decision_id"),
            "decision_hash":decision.get("decision_hash"),
            "market_as_of":decision_date,
            "frozen_at":decision.get("frozen_at"),
            "observation_days":len(future),
            "incomplete_outcome_dates":sorted(set(x for x in incomplete_outcome_dates if x)),
            "daily_path":daily,
            "current_triaid_theoretical_return":self._compound(target_daily),
            "current_baseline_theoretical_return":self._compound(baseline_daily),
            "current_benchmark_buy_hold_return":self._compound(benchmark_daily),
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
        latest_date=str(latest.get("market_as_of") or "")
        prior_frozen=[
            row for row in rows[:-1]
            if str(row.get("decision_status") or "")=="DAILY_FROZEN"
            and str(row.get("market_as_of") or "")<latest_date
        ]
        previous=prior_frozen[-1] if prior_frozen else None
        return {
            "report_version":self.version,
            "route_version":latest.get("route_version"),
            "latest_decision":deepcopy(latest),
            "latest_decision_review":self.review_decision(latest),
            "previous_decision_review":self.review_decision(previous) if previous else None,
            "latest_outcome_as_of":self.outcomes(1)[-1].get("as_of") if self.outcomes(1) else None,
            "integrity":self.verify_integrity(),
            "interpretation_guard":"HK Return-Max uses a frozen HK-only strategy baseline, TRIAID weights, and four HKD sleeves. Theoretical holdings and simulated-execution sleeve returns are reported separately; modeled impact and fills are not broker executions.",
        }

    def status(self)->dict:
        return {
            "version":self.version,
            "decision_count":len(self.decisions(5000)),
            "outcome_count":len(self.outcomes(5000)),
            "integrity":self.verify_integrity(),
        }

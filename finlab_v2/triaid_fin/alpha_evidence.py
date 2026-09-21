from __future__ import annotations

import json
import math
from copy import deepcopy
from math import prod
from statistics import mean

from .adaptive_alpha import promotion_gate
from .contracts import utc_now
from .store import RunStore


class AlphaEvidenceLedger:
    """Prospective paired evidence for market-vs-algorithm and challenger promotion.

    This ledger never changes portfolio weights. It only records outcomes that
    became observable after a decision had already been frozen.
    """

    version="alpha-evidence-ledger@0.1.0"
    file="alpha_evidence.jsonl"
    index_file="alpha_evidence_index.json"

    def __init__(self,store:RunStore)->None:
        self.store=store
        self.index=store.load_json(self.index_file,default={}) or {}

    def rows(self,market_id:str|None=None,limit:int=5000)->list[dict]:
        rows=self.store.read_jsonl(self.file,limit=limit)
        if market_id:
            rows=[r for r in rows if str(r.get("market_id") or "").upper()==market_id.upper()]
        return rows

    @staticmethod
    def _compound(xs:list[float])->float:
        return prod(1.0+float(x) for x in xs)-1.0 if xs else 0.0

    @staticmethod
    def _max_drawdown(xs:list[float])->float:
        equity=1.0;peak=1.0;worst=0.0
        for r in xs:
            equity*=1.0+float(r)
            peak=max(peak,equity)
            worst=min(worst,equity/peak-1.0)
        return worst

    @staticmethod
    def _weighted(weights:dict[str,float],returns:dict[str,float])->float:
        return sum(float(w)*float(returns.get(k,0.0)) for k,w in weights.items())

    def _append_once(self,key:str,row:dict)->dict:
        if key in self.index:
            return {"recorded":False,"reason":"DUPLICATE_EVIDENCE","evidence_id":self.index[key]}
        row=deepcopy(row)
        row["evidence_id"]=f"AE-{len(self.rows(None,100000))+1:08d}"
        row["recorded_at"]=utc_now()
        self.store.append_jsonl(self.file,row)
        self.index[key]=row["evidence_id"]
        self.store.save_json(self.index_file,self.index)
        return {"recorded":True,"evidence":row}

    @staticmethod
    def _decision_for_period(decisions:list[dict],period_start_as_of:str)->dict|None:
        rows=[
            d for d in decisions
            if str(d.get("market_as_of") or "")==str(period_start_as_of)
            and str(d.get("decision_status") or "")=="DAILY_FROZEN"
        ]
        return rows[-1] if rows else None

    def record_us(
        self,
        decisions:list[dict],
        outcome:dict,
    )->dict:
        decision=self._decision_for_period(decisions,str(outcome.get("period_start_as_of") or ""))
        if not decision:
            return {"recorded":False,"reason":"NO_FROZEN_DECISION_FOR_PERIOD"}
        sr={str(k):float(v) for k,v in (outcome.get("strategy_returns") or {}).items()}
        pr={str(k):float(v) for k,v in (outcome.get("product_returns") or {}).items()}
        primary=str(decision.get("selected_strategy_id") or "")
        challenger=str((decision.get("fast_challenger") or {}).get("challenger_strategy_id") or "")
        if primary not in sr:
            return {"recorded":False,"reason":"PRIMARY_RETURN_MISSING"}
        primary_r=sr[primary]
        challenger_r=sr.get(challenger) if challenger else None
        benchmark=pr.get("SPY")
        feasible={k:v for k,v in sr.items() if k!="P28_CASH"}
        hindsight_id=max(feasible,key=feasible.get) if feasible else None
        hindsight_r=feasible.get(hindsight_id) if hindsight_id else None
        row={
            "market_id":"US",
            "as_of":outcome.get("as_of"),
            "period_start_as_of":outcome.get("period_start_as_of"),
            "decision_id":decision.get("decision_id"),
            "evidence_type":"PAIRED_PRIMARY_CHALLENGER_AND_BETA",
            "primary_strategy_id":primary,
            "challenger_strategy_id":challenger or None,
            "primary_return":primary_r,
            "challenger_return":challenger_r,
            "paired_challenger_excess":(
                float(challenger_r)-primary_r if challenger_r is not None else None
            ),
            "market_beta_return":benchmark,
            "algorithm_excess_vs_market":(
                primary_r-float(benchmark) if benchmark is not None else None
            ),
            "hindsight_best_strategy_id":hindsight_id,
            "hindsight_best_return":hindsight_r,
            "missed_opportunity_cost":(
                float(hindsight_r)-primary_r if hindsight_r is not None else None
            ),
            "lookahead_or_same_bar_leakage":False,
            "promotion_eligible_observation":challenger_r is not None,
        }
        return self._append_once(f"US:{outcome.get('as_of')}",row)

    def record_cn(
        self,
        decisions:list[dict],
        outcome:dict,
    )->dict:
        decision=self._decision_for_period(decisions,str(outcome.get("period_start_as_of") or ""))
        if not decision:
            return {"recorded":False,"reason":"NO_FROZEN_DECISION_FOR_PERIOD"}
        returns={str(k):float(v) for k,v in (outcome.get("product_returns") or {}).items()}
        opinions=decision.get("trade_opinions") or []
        strict_weights={
            str(x.get("symbol")):float(x.get("target_weight") or 0.0)
            for x in opinions if x.get("symbol")
        }
        products=[s for s in strict_weights if s in returns]
        if not products:
            return {"recorded":False,"reason":"PRODUCT_RETURNS_MISSING"}
        strict_r=self._weighted(strict_weights,returns)
        market_beta=mean([returns[s] for s in products])
        soft=decision.get("soft_recovery_shadow") or {}
        eligible=[s for s in (soft.get("eligible_symbols") or []) if s in returns]
        budget=float(soft.get("max_shadow_probe_budget") or 0.0)
        soft_weights={s:(budget/len(eligible)) for s in eligible} if eligible else {}
        soft_r=self._weighted(soft_weights,returns)
        row={
            "market_id":"CN",
            "as_of":outcome.get("as_of"),
            "period_start_as_of":outcome.get("period_start_as_of"),
            "decision_id":decision.get("decision_id"),
            "evidence_type":"STRICT_DEFENSE_VS_SOFT_RECOVERY_AND_MARKET",
            "strict_portfolio_return":strict_r,
            "market_beta_return":market_beta,
            "strict_algorithm_excess_vs_market":strict_r-market_beta,
            "soft_probe_return":soft_r,
            "soft_probe_budget":budget,
            "soft_probe_symbols":eligible,
            "soft_probe_incremental_vs_strict":soft_r-strict_r,
            "defense_opportunity_cost":max(0.0,market_beta-strict_r),
            "defense_avoided_loss":max(0.0,strict_r-market_beta),
            "lookahead_or_same_bar_leakage":False,
            "promotion_eligible_observation":bool(eligible),
        }
        return self._append_once(f"CN:{outcome.get('as_of')}",row)

    def promotion_status(self,market_id:str)->dict:
        market=market_id.upper()
        rows=[r for r in self.rows(market,5000) if r.get("promotion_eligible_observation")]
        if market=="US":
            paired=[float(r["paired_challenger_excess"]) for r in rows if r.get("paired_challenger_excess") is not None]
            primary=[float(r["primary_return"]) for r in rows if r.get("paired_challenger_excess") is not None]
            challenger=[float(r["challenger_return"]) for r in rows if r.get("paired_challenger_excess") is not None]
        else:
            paired=[float(r["soft_probe_incremental_vs_strict"]) for r in rows if r.get("soft_probe_incremental_vs_strict") is not None]
            primary=[float(r["strict_portfolio_return"]) for r in rows if r.get("soft_probe_incremental_vs_strict") is not None]
            challenger=[float(r["soft_probe_return"]) for r in rows if r.get("soft_probe_incremental_vs_strict") is not None]

        n=len(paired)
        rolling=[]
        window=10
        for i in range(window,n+1):
            rolling.append(sum(paired[i-window:i])>0)
        maxdd_primary=self._max_drawdown(primary)
        maxdd_challenger=self._max_drawdown(challenger)
        metrics={
            "trading_days":n,
            "independent_decisions":n,
            "paired_net_excess_per_decision":mean(paired) if paired else None,
            "excess_win_rate":(sum(1 for x in paired if x>0)/n) if n else None,
            "positive_rolling_windows_ratio":(
                sum(1 for x in rolling if x)/len(rolling) if rolling else 0.0
            ),
            "max_drawdown_deterioration":max(0.0,maxdd_primary-maxdd_challenger),
            # Turnover/capacity/risk are fail-closed until paired execution evidence is available.
            "turnover_multiplier":None,
            "capacity_pass":False,
            "liquidity_pass":False,
            "risk_pass":False,
            "lookahead_or_same_bar_leakage":False,
        }
        gate=promotion_gate(metrics,"shadow_to_pilot")
        blockers=[k for k,v in gate["checks"].items() if not v]
        return {
            "version":self.version,
            "market_id":market,
            "observations":n,
            "metrics":metrics,
            "shadow_to_pilot_gate":gate,
            "blockers":blockers,
            "discipline":"FAIL_CLOSED_UNTIL_PAIRED_EXECUTION_COST_CAPACITY_LIQUIDITY_AND_RISK_EVIDENCE_ARE_AVAILABLE",
        }

    def attribution_summary(self,market_id:str)->dict:
        market=market_id.upper()
        rows=self.rows(market,5000)
        if market=="US":
            market_returns=[float(r["market_beta_return"]) for r in rows if r.get("market_beta_return") is not None]
            algo_excess=[float(r["algorithm_excess_vs_market"]) for r in rows if r.get("algorithm_excess_vs_market") is not None]
            missed=[float(r["missed_opportunity_cost"]) for r in rows if r.get("missed_opportunity_cost") is not None]
            return {
                "market_id":"US",
                "observations":len(algo_excess),
                "market_beta_cumulative_return":self._compound(market_returns),
                "algorithm_excess_arithmetic_sum":sum(algo_excess),
                "missed_opportunity_arithmetic_sum":sum(missed),
            }
        market_returns=[float(r["market_beta_return"]) for r in rows if r.get("market_beta_return") is not None]
        strict_excess=[float(r["strict_algorithm_excess_vs_market"]) for r in rows if r.get("strict_algorithm_excess_vs_market") is not None]
        defense_cost=[float(r["defense_opportunity_cost"]) for r in rows if r.get("defense_opportunity_cost") is not None]
        avoided=[float(r["defense_avoided_loss"]) for r in rows if r.get("defense_avoided_loss") is not None]
        return {
            "market_id":"CN",
            "observations":len(strict_excess),
            "market_beta_cumulative_return":self._compound(market_returns),
            "algorithm_excess_arithmetic_sum":sum(strict_excess),
            "defense_opportunity_cost_sum":sum(defense_cost),
            "defense_avoided_loss_sum":sum(avoided),
        }

    def status(self)->dict:
        return {
            "version":self.version,
            "rows":len(self.rows(None,100000)),
            "US":self.promotion_status("US"),
            "CN":self.promotion_status("CN"),
            "attribution":{
                "US":self.attribution_summary("US"),
                "CN":self.attribution_summary("CN"),
            },
        }

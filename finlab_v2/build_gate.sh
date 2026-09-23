#!/bin/sh
set -eu

python -m py_compile app.py triaid_fin/*.py *.py
python selftest.py
python daily_report_change_attribution_smoke.py
python long_cycle_hypothesis_smoke.py
python cross_market_crash_smoke.py
python latent_hazard_smoke.py
python policy_curve_smoke.py
python hazard_prospective_smoke.py
python risk_warning_smoke.py
python risk_control_smoke.py
python hk_market_smoke.py
python hk_high_frequency_degradation_smoke.py
python core_evolution_validation_smoke.py
python backend_audit_regression.py
python us_return_max_smoke.py
python strategy_contract_smoke.py
python production_smoke_contract_static.py
echo TRIAID_BUILD_GATE_PASS

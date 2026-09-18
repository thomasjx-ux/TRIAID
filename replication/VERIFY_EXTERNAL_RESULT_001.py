import json, sys

def derive_gates(r):
    o=r['overall']; f=r['family']; d=r['paired_safe_recovery_differences']
    dm=lambda k: float(d[k]['mean'])
    fm=lambda p,fam,m: float(f[f'{p}|{fam}'][m])
    return {
      'G1_safety':float(o['triaid']['unsafe'])<=0.05,
      'G2_static_sep':dm('static')>=0.20,
      'G3_graph_sep':dm('graph')>=0.08,
      'G4_prop_necessity':dm('no_prop')>=0.08,
      'G5_mpc_tie':abs(dm('generic_mpc'))<=0.04,
      'G6_common_mode':fm('triaid','common_mode','safe_recovery')-fm('no_prop','common_mode','safe_recovery')>=0.25,
      'G7_actuation_verify':fm('triaid','actuation_mismatch','unsafe')<=0.05 and fm('graph','actuation_mismatch','unsafe')-fm('triaid','actuation_mismatch','unsafe')>=0.15,
      'G8_recovery_cert':fm('graph','recovery_mismatch','premature')-fm('triaid','recovery_mismatch','premature')>=0.30,
      'G9_stale_restraint':fm('triaid','stale_info','unsafe')<=fm('graph','stale_info','unsafe'),
      'G10_accounting': all(m in o[p] for p in o for m in ('cost','collateral'))
    }

def main():
    if len(sys.argv)!=2:
        raise SystemExit('usage: python VERIFY_EXTERNAL_RESULT_001.py external_result.json')
    r=json.load(open(sys.argv[1]))
    gates=derive_gates(r)
    declared=r.get('gates') or {}
    declared_match=all(declared.get(k)==v for k,v in gates.items())
    out={'controlling_gate_result':'PASS_ALL_GATES' if all(gates.values()) else 'FAIL_ONE_OR_MORE_GATES','derived_gates':gates,'declared_gate_fields_match':declared_match}
    print(json.dumps(out,indent=2,sort_keys=True))
    raise SystemExit(0 if all(gates.values()) and declared_match else 2)

if __name__=='__main__': main()

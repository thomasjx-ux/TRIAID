#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

TRACKS = {"REPRODUCE", "CHALLENGE", "VALIDATE"}
VERDICTS = {"PASS", "FAIL", "PARTIAL", "INCONCLUSIVE", "OUT_OF_SCOPE"}
BASELINE = {"NOT_TESTED", "TRIAID_WINS", "BASELINE_WINS", "MIXED", "TIE", "NOT_APPLICABLE"}
IMPACTS = {"NO_CHANGE", "SUPPORTS_WITHIN_SCOPE", "NARROWS_CLAIM", "FALSIFIES_COMPONENT", "REQUIRES_CORRECTION", "NO_INCREMENTAL_VALUE"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_TOP = {"schema_version", "submission_id", "track", "title", "submitter", "target", "design", "environment", "data", "result", "claim_impact", "artifacts", "checksums", "declarations"}

class ValidationError(Exception):
    pass

def need(obj, key, where):
    if key not in obj:
        raise ValidationError(f"missing {where}.{key}")
    return obj[key]

def nonempty_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")

def validate(doc):
    if not isinstance(doc, dict):
        raise ValidationError("root must be a JSON object")
    missing = REQUIRED_TOP - set(doc)
    if missing:
        raise ValidationError("missing top-level fields: " + ", ".join(sorted(missing)))
    if doc["schema_version"] != "triaid.open_validation.v1":
        raise ValidationError("unsupported schema_version")
    nonempty_string(doc["submission_id"], "submission_id")
    nonempty_string(doc["title"], "title")
    if doc["track"] not in TRACKS:
        raise ValidationError(f"track must be one of {sorted(TRACKS)}")

    s = doc["submitter"]
    for k in ["name_or_handle", "organization", "independent_of_triaid_authors", "conflicts_or_relationships"]:
        need(s, k, "submitter")
    if not isinstance(s["independent_of_triaid_authors"], bool):
        raise ValidationError("submitter.independent_of_triaid_authors must be boolean")

    t = doc["target"]
    for k in ["artifact_name", "artifact_sha256", "claim_or_test", "frozen_protocol_or_section"]:
        need(t, k, "target")
    nonempty_string(t["artifact_name"], "target.artifact_name")
    if not SHA256_RE.match(t["artifact_sha256"]):
        raise ValidationError("target.artifact_sha256 must be 64 lowercase hex characters")
    nonempty_string(t["claim_or_test"], "target.claim_or_test")

    d = doc["design"]
    for k in ["confirmatory", "protocol_frozen_before_locked_outcomes", "changes_from_canonical_target", "outcomes_seen_before_changes", "matched_baselines", "failure_criteria"]:
        need(d, k, "design")
    for k in ["confirmatory", "protocol_frozen_before_locked_outcomes", "outcomes_seen_before_changes"]:
        if not isinstance(d[k], bool):
            raise ValidationError(f"design.{k} must be boolean")
    for k in ["changes_from_canonical_target", "matched_baselines", "failure_criteria"]:
        if not isinstance(d[k], list):
            raise ValidationError(f"design.{k} must be a list")
    if d["confirmatory"] and not d["protocol_frozen_before_locked_outcomes"]:
        raise ValidationError("confirmatory=true requires protocol_frozen_before_locked_outcomes=true")
    if d["confirmatory"] and d["outcomes_seen_before_changes"] and d["changes_from_canonical_target"]:
        raise ValidationError("confirmatory submission cannot contain post-outcome target changes")

    e = doc["environment"]
    for k in ["os", "hardware", "python", "dependencies", "commands"]:
        need(e, k, "environment")
    if not isinstance(e["commands"], list):
        raise ValidationError("environment.commands must be a list")

    data = doc["data"]
    for k in ["source", "public_or_shareable", "provenance", "permissions_or_ethics", "split_or_holdout"]:
        need(data, k, "data")
    if not isinstance(data["public_or_shareable"], bool):
        raise ValidationError("data.public_or_shareable must be boolean")

    r = doc["result"]
    for k in ["verdict", "evidence_level", "summary", "metrics", "negative_results", "deviations", "baseline_outcome"]:
        need(r, k, "result")
    if r["verdict"] not in VERDICTS:
        raise ValidationError(f"result.verdict must be one of {sorted(VERDICTS)}")
    if not isinstance(r["evidence_level"], int) or not 0 <= r["evidence_level"] <= 8:
        raise ValidationError("result.evidence_level must be integer 0..8")
    nonempty_string(r["summary"], "result.summary")
    if not isinstance(r["metrics"], dict):
        raise ValidationError("result.metrics must be an object")
    if not isinstance(r["negative_results"], list) or not isinstance(r["deviations"], list):
        raise ValidationError("result.negative_results and result.deviations must be lists")
    if r["baseline_outcome"] not in BASELINE:
        raise ValidationError(f"result.baseline_outcome must be one of {sorted(BASELINE)}")

    c = doc["claim_impact"]
    for k in ["suggested", "scope", "reason"]:
        need(c, k, "claim_impact")
    if c["suggested"] not in IMPACTS:
        raise ValidationError(f"claim_impact.suggested must be one of {sorted(IMPACTS)}")

    if not isinstance(doc["artifacts"], list):
        raise ValidationError("artifacts must be a list")
    if not isinstance(doc["checksums"], dict):
        raise ValidationError("checksums must be an object")
    for name, h in doc["checksums"].items():
        if not isinstance(name, str) or not SHA256_RE.match(h):
            raise ValidationError(f"checksums[{name!r}] must be a lowercase SHA-256")

    dec = doc["declarations"]
    for k in ["no_private_or_unauthorized_data", "no_post_outcome_relabeling_as_confirmatory", "evidence_level_not_overstated"]:
        if need(dec, k, "declarations") is not True:
            raise ValidationError(f"declarations.{k} must be true")

    if r["evidence_level"] == 8 and not s["independent_of_triaid_authors"]:
        raise ValidationError("evidence_level=8 requires independent_of_triaid_authors=true")

    return True

def main(argv):
    if len(argv) != 2:
        print("usage: validate_result.py RESULT.json", file=sys.stderr)
        return 2
    p = Path(argv[1])
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        validate(doc)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"OPEN_VALIDATION_RESULT_INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OPEN_VALIDATION_RESULT_VALID: {p}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

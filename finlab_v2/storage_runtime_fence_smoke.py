from pathlib import Path

text=Path("triaid_fin/storage_backend.py").read_text(encoding="utf-8")
policy=Path("triaid_fin/persistence_policy.py").read_text(encoding="utf-8")
start=Path("start.sh").read_text(encoding="utf-8")

assert 'TRIAID_RUNTIME_ID' in policy
assert 'TRIAID_RUNTIME_ROLE' in policy
assert 'TRIAID_PERSISTENCE_SCOPE' in policy
assert 'TRIAID_WRITER_ACTIVATION_REQUIRED' in policy
assert '"x-triaid-runtime-id":self.runtime_id' in text
assert '"x-triaid-runtime-role":self.runtime_role' in text
assert '"x-triaid-persistence-scope":self.persistence_scope' in text
assert '"x-triaid-service-id":self.service_id' in text
assert '"x-triaid-deployment-id":self.deployment_id' in text
assert 'supabase-storage-backend@0.3.0' in text
assert 'triaid-persistence-contract@1.0.0' in text
assert 'TRIAID-FIN-V2-STORAGE/0.3' in text
assert 'official_persistence_write_fenced' in text
assert 'official_writer_not_activated' in text
assert 'activate_official_writer.py' in start
assert 'TRIAID_WRITER_ACTIVATION_BLOCKED_DEPLOY' in start
assert 'TRIAID_SUPABASE_TIMEOUT_SECONDS' in text
assert 'min(int(timeout),120)' in text

print("TRIAID_STORAGE_RUNTIME_FENCE_SMOKE_PASS")

from pathlib import Path

text=Path("triaid_fin/storage_backend.py").read_text(encoding="utf-8")

assert 'TRIAID_RUNTIME_ID' in text
assert '"x-triaid-runtime-id":self.runtime_id' in text
assert 'supabase-storage-backend@0.2.1' in text
assert 'TRIAID-FIN-V2-STORAGE/0.2' in text
assert 'TRIAID_SUPABASE_TIMEOUT_SECONDS' in text
assert 'min(int(timeout),120)' in text

print("TRIAID_STORAGE_RUNTIME_FENCE_SMOKE_PASS")

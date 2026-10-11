import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import journal

print(json.dumps([
    {key: row.get(key) for key in ("operation_id", "kind", "status", "factor_code", "message")}
    for row in journal.list_recent(15)
], ensure_ascii=False, default=str, indent=2))

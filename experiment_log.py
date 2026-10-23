"""Readable console transcript plus one JSONL file per execution experiment."""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4


LOG_DIR = Path(__file__).with_name("logs")


class ExperimentLog:
    """Record visible exchanges and results, without credentials or hidden reasoning."""

    def __init__(self, directory: Path = LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = directory / f"{stamp}-{uuid4().hex[:8]}.jsonl"
        self._stream = self.path.open("x", encoding="utf-8")
        self.sequence = 0
        print(f"Experiment log: {self.path}")

    def emit(self, event: str, **data) -> None:
        self.sequence += 1
        record = {"sequence": self.sequence, "timestamp": datetime.now(timezone.utc).isoformat(),
                  "event": event, **data}
        self._stream.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
        self._stream.flush()
        print(f"\n[{event.upper().replace('_', ' ')}]")
        if event == "natural_language":
            print(data["text"])
            if data.get("usage") is not None:
                print("Usage: " + json.dumps(data["usage"]))
        elif event in ("model_sql", "sql_execution"):
            print(data["sql"])
            print(json.dumps({key: value for key, value in data.items() if key != "sql"}, indent=2))
        else:
            print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stream.close()

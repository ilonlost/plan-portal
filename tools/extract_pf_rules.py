"""Extract the supplied PF mapping without changing its workbook."""
import json
import sys
from pathlib import Path

from openpyxl import load_workbook


def extract(source: str, destination: str) -> None:
    workbook = load_workbook(source, read_only=True, data_only=True)
    keys = ("gp_sku", "gp_name", "buffer", "pf_cost_center", "pf_sku", "pf_name",
            "filling_workshop", "filling_sku", "filling_name", "note", "workshop")
    rows = []
    for number, row in enumerate(workbook.worksheets[0].values, 1):
        if number < 4 or not row[1]:
            continue
        record = dict(zip(keys, row[1:12]))
        for key in ("gp_sku", "pf_sku", "filling_sku", "pf_cost_center"):
            record[key] = str(record[key]) if record[key] is not None else None
        record["source_row"] = number
        rows.append(record)
    speeds = []
    for row in list(workbook.worksheets[1].values)[3:]:
        if row[1] and row[4]:
            speeds.append({"gp_sku": str(row[1]), "gp_speed": row[3],
                           "pf_sku": str(row[4]), "pf_speed": row[6]})
    data = {"source": Path(source).name, "rows": rows, "speeds": speeds}
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workbook.close()
    print(f"Extracted {len(rows)} source rows and {len(speeds)} speed examples")


if __name__ == "__main__":
    extract(*sys.argv[1:])

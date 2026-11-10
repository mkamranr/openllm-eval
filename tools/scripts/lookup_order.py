"""Example script tool: look up an order in a read-only fixture (data/orders.json).

Input (stdin):  {"order_id": "A-1001"}
Output (stdout): the order, or {"error": "order not found"}
"""

import json
import sys
from pathlib import Path

ORDERS = Path(__file__).resolve().parent.parent / "data" / "orders.json"


def main():
    arguments = json.load(sys.stdin)
    orders = json.loads(ORDERS.read_text(encoding="utf-8"))
    order = orders.get(arguments["order_id"])
    print(json.dumps(order if order is not None else {"error": "order not found"}))


if __name__ == "__main__":
    main()

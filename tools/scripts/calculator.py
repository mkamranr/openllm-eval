"""Example script tool: evaluate an arithmetic expression safely (no eval).

Input (stdin):  {"expression": "2 * (3 + 4)"}
Output (stdout): {"result": 14}
"""

import ast
import json
import operator
import sys

OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def evaluate(node):
    if isinstance(node, ast.Expression):
        return evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        left, right = evaluate(node.left), evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATORS:
        return OPERATORS[type(node.op)](evaluate(node.operand))
    raise ValueError("only numbers, + - * / // % ** and parentheses are allowed")


def main():
    arguments = json.load(sys.stdin)
    try:
        value = evaluate(ast.parse(arguments["expression"], mode="eval"))
    except ZeroDivisionError:
        print(json.dumps({"error": "division by zero"}))
        return
    except (SyntaxError, ValueError, TypeError, OverflowError) as exc:
        print(json.dumps({"error": f"invalid expression: {exc}"}))
        return
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    print(json.dumps({"result": value}))


if __name__ == "__main__":
    main()

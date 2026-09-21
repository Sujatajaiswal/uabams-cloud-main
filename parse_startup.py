import ast

with open("app/main.py") as f:
    tree = ast.parse(f.read())

for node in ast.walk(tree):
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "startup":
        for stmt in node.body:
            print(type(stmt).__name__)

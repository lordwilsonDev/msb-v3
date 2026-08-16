"""Parser tests — Python via real ast, other languages approximate."""

from msb_v3.codegraph.parser import language_for, parse_source


def _kinds(result) -> list[str]:
    return sorted(n["kind"] for n in result.nodes)


def _fqs(result) -> list[str]:
    return sorted(n["fq_name"] for n in result.nodes)


def test_python_extracts_functions_classes_methods():
    src = '''
import math

def top(x):
    return x + 1

class Worker:
    def run(self):
        return top(1)

def helper(a, b):
    return a + b
'''
    result = parse_source(src, "mod.py", "python")
    kinds = _kinds(result)
    assert "function" in kinds and "class" in kinds and "method" in kinds
    assert "mod.top" in _fqs(result)
    assert "mod.Worker" in _fqs(result)
    assert "mod.Worker.run" in _fqs(result)


def test_python_import_edges():
    src = "from sample_repo.utils import helper\nimport math\n"
    result = parse_source(src, "mod.py", "python")
    edges = [(e["relation"], e["source"], e["target"]) for e in result.edges if e["relation"] == "imports"]
    assert ("imports", "mod", "sample_repo.utils.helper") in edges
    assert ("imports", "mod", "math") in edges


def test_python_call_edges_resolve():
    src = '''
def helper(a, b):
    return a + b

def caller():
    return helper(1, 2)
'''
    result = parse_source(src, "mod.py", "python")
    calls = [(e["source"], e["target"]) for e in result.edges if e["relation"] == "calls"]
    assert ("mod.caller", "mod.helper") in calls


def test_python_inherits_edge():
    src = '''
class Base:
    pass

class Child(Base):
    pass
'''
    result = parse_source(src, "mod.py", "python")
    inh = [(e["source"], e["target"]) for e in result.edges if e["relation"] == "inherits"]
    assert ("mod.Child", "mod.Base") in inh


def test_python_unresolvable_call_is_skipped():
    src = "def f():\n    return some_undefined_thing(1)\n"
    result = parse_source(src, "mod.py", "python")
    calls = [e for e in result.edges if e["relation"] == "calls"]
    assert calls == []  # never guess


def test_python_syntax_error_returns_empty():
    result = parse_source("def broken(:\n", "mod.py", "python")
    assert result.nodes == [] and result.edges == []


def test_typescript_approximate_flags():
    src = 'import { helper } from "./lib";\n\nfunction greet(name: string): string {\n  return "hi " + name;\n}\n'
    result = parse_source(src, "app.ts", "typescript")
    funcs = [n for n in result.nodes if n["kind"] == "function"]
    assert funcs and all(n["approximate"] for n in funcs)
    assert any(n["name"] == "greet" for n in funcs)


def test_ts_class_and_methods_approximate():
    src = "class Greeter {\n  greet(): string {\n    return 'hi';\n  }\n}\n"
    result = parse_source(src, "app.ts", "typescript")
    kinds = _kinds(result)
    assert "class" in kinds
    assert any(n["name"] == "Greeter" and n["kind"] == "class" for n in result.nodes)


def test_language_detection():
    assert language_for("foo.py") == "python"
    assert language_for("app.tsx") == "typescript"
    assert language_for("main.go") == "go"
    assert language_for("README.md") is None


def test_unknown_language_empty():
    result = parse_source("whatever", "notes.md")
    assert result.nodes == [] and result.edges == []

"""Stdlib-only code parser for the Code Graph subsystem.

Python is parsed with the real ``ast`` module — accurate extraction of
functions, classes, methods, imports, static calls, and inheritance.
Everything else (js/ts/go/rust/java/…) uses per-language regex heuristics
and every node produced that way is flagged ``approximate``. The graph is
a static approximation by design; the flag makes that honest and
queryable (``approximate=0`` nodes are the trustworthy core).

Resolution rules (Python, static best-effort):

- A ``calls`` edge is emitted only when the callee resolves to a symbol
  defined in this file (or a dotted path rooted at an import alias we
  saw). Unresolvable calls are skipped — never guessed.
- ``references`` edges are emitted for Name loads that resolve to a
  symbol defined in the same file.
- ``imports`` edges carry the imported fq name; ``inherits`` edges link
  a class to a base that resolves within the file.

Each file yields (nodes, edges). The indexer owns repo-level wiring
(module fq names, ``contains`` edges from module to its top-levels).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# language -> file extensions (first match wins in the indexer)
LANGUAGE_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx", ".mts"),
    "go": (".go",),
    "rust": (".rs",),
    "java": (".java",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".hpp", ".cxx"),
    "csharp": (".cs",),
    "ruby": (".rb",),
    "php": (".php",),
    "shell": (".sh", ".bash"),
}

_EXT_TO_LANG: Dict[str, str] = {
    ext: lang for lang, exts in LANGUAGE_EXTENSIONS.items() for ext in exts
}


def language_for(path: str) -> Optional[str]:
    return _EXT_TO_LANG.get(Path(path).suffix.lower())


@dataclass
class ParseResult:
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Python (real AST)
# ---------------------------------------------------------------------------

def _py_module_fq(rel_path: str) -> str:
    """package/foo.py -> package.foo ; package/__init__.py -> package."""
    parts = Path(rel_path).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "root"


class _PyExtractor(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.module_fq = _py_module_fq(rel_path)
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        # fq_name -> node for resolution
        self.defined: Dict[str, Dict[str, Any]] = {}
        # short name -> fq for the current scope (call resolution)
        self.scope_names: Dict[str, str] = {}
        self._class_stack: List[str] = []
        self._func_stack: List[str] = []

    # -- helpers ---------------------------------------------------------

    def _add_node(
        self, kind: str, name: str, fq_name: str, line: int, col: int, signature: str = ""
    ) -> Dict[str, Any]:
        node = {
            "kind": kind,
            "name": name,
            "fq_name": fq_name,
            "file": self.rel_path,
            "line": line,
            "col": col,
            "signature": signature,
            "approximate": False,
        }
        self.nodes.append(node)
        self.defined[fq_name] = node
        self.scope_names[name] = fq_name
        return node

    def _add_edge(self, relation: str, source: str, target: str, line: int) -> None:
        self.edges.append(
            {
                "relation": relation,
                "source": source,
                "target": target,
                "file": self.rel_path,
                "line": line,
            }
        )

    @property
    def _current_scope(self) -> str:
        """The innermost scope edges originate from: function > class > module."""
        if self._func_stack:
            return self._func_stack[-1]
        if self._class_stack:
            return self._class_stack[-1]
        return self.module_fq

    def _resolve(self, name: str) -> Optional[str]:
        """Resolve a short name (or dotted path) to a symbol fq_name.

        Order: a symbol defined in this file wins; otherwise an import
        alias resolves to its import target (cross-module calls resolve
        through the imports we saw). Unresolvable names return None — never
        guessed."""
        if name in self.defined:
            return name
        head = name.split(".")[0]
        if head in self.scope_names:
            base = self.scope_names[head]
            suffix = name[len(head):]
            candidate = base + suffix
            if candidate in self.defined:
                return candidate
            # plain alias use (no dotted suffix): resolve to the import target
            if head == name and base:
                return base
        return None

    # -- visitors --------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            asname = alias.asname or alias.name.split(".")[0]
            self.scope_names[asname] = alias.name
            self._add_edge("imports", self.module_fq, alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            fq = f"{base}.{alias.name}" if base else alias.name
            asname = alias.asname or alias.name
            self.scope_names[asname] = fq
            self._add_edge("imports", self.module_fq, fq, node.lineno)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent = ".".join(self._class_stack) if self._class_stack else self.module_fq
        fq = f"{parent}.{node.name}" if parent else node.name
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
                resolved = self._resolve(base.id)
                if resolved and resolved != fq:
                    self._add_edge("inherits", fq, resolved, node.lineno)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.unparse(base))
                resolved = self._resolve(ast.unparse(base))
                if resolved and resolved != fq:
                    self._add_edge("inherits", fq, resolved, node.lineno)
        signature = ", ".join(bases)
        self._add_node("class", node.name, fq, node.lineno, node.col_offset, signature=signature)
        self._class_stack.append(fq)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent = ".".join(self._class_stack) if self._class_stack else self.module_fq
        fq = f"{parent}.{node.name}" if parent else node.name
        args = [a.arg for a in node.args.args]
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        signature = f"({', '.join(args)})"
        kind = "method" if self._class_stack else "function"
        self._add_node(kind, node.name, fq, node.lineno, node.col_offset, signature=signature)
        self._func_stack.append(fq)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        current = self._current_scope
        if isinstance(node.func, ast.Name):
            resolved = self._resolve(node.func.id)
            if resolved and resolved != current:
                self._add_edge("calls", current, resolved, node.lineno)
        elif isinstance(node.func, ast.Attribute):
            full = ast.unparse(node.func)
            resolved = self._resolve(full)
            if resolved and resolved != current:
                self._add_edge("calls", current, resolved, node.lineno)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # A load of a name defined in this file is a reference edge.
        if isinstance(node.ctx, ast.Load):
            resolved = self._resolve(node.id)
            if resolved:
                current = self._current_scope
                if resolved != current:
                    self._add_edge("references", current, resolved, node.lineno)
        self.generic_visit(node)


def parse_python(source: str, rel_path: str) -> ParseResult:
    result = ParseResult()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return result
    extractor = _PyExtractor(rel_path)
    extractor.visit(tree)
    result.nodes.extend(extractor.nodes)
    result.edges.extend(extractor.edges)
    return result


# ---------------------------------------------------------------------------
# Other languages (regex heuristics — labeled approximate)
# ---------------------------------------------------------------------------

# language -> (declaration regexes, import regex)
# Declaration captures: name, and optionally class-name for methods.
_LANG_PATTERNS: Dict[str, Dict[str, Any]] = {
    "javascript": {
        "function": re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
        "arrow": re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
        "class": re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"),
        "method": re.compile(r"\b(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"),
        "import": re.compile(r"\bimport\s+[\w$*{,\s]+?\s+from\s+['\"]([^'\"]+)['\"]"),
    },
    "typescript": {
        "function": re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
        "arrow": re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
        "class": re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"),
        "interface": re.compile(r"\binterface\s+([A-Za-z_$][\w$]*)"),
        "type": re.compile(r"\btype\s+([A-Za-z_$][\w$]*)\s*="),
        "method": re.compile(r"\b(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*[:{]\s*(?:[A-Za-z_$][\w$<>[\]|&]*\s*)?\{"),
        "import": re.compile(r"\bimport\s+[\w$*{,\s]+?\s+from\s+['\"]([^'\"]+)['\"]"),
    },
    "go": {
        "function": re.compile(r"\bfunc\s+([A-Za-z_]\w*)\s*\("),
        "method": re.compile(r"\bfunc\s+\([^)]*\)\s+([A-Za-z_]\w*)\s*\("),
        "type": re.compile(r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface|map|\[\]|chan|\w+)"),
        "import": re.compile(r"\bimport\s*\(?\s*[\"]([^\"\s]+)[\"]"),
    },
    "rust": {
        "function": re.compile(r"\bfn\s+([a-z_]\w*)\s*\("),
        "struct": re.compile(r"\bstruct\s+([A-Z]\w*)"),
        "enum": re.compile(r"\benum\s+([A-Z]\w*)"),
        "impl": re.compile(r"\bimpl\s+([A-Z]\w*)"),
        "type": re.compile(r"\btype\s+([A-Za-z_]\w*)\s*="),
        "use": re.compile(r"\buse\s+([a-zA-Z_:]+)"),
    },
    "java": {
        "class": re.compile(r"\b(?:public|private|protected|final|abstract)?\s*class\s+([A-Z]\w*)"),
        "interface": re.compile(r"\binterface\s+([A-Z]\w*)"),
        "method": re.compile(r"\b(?:public|private|protected|static|final|synchronized|abstract)?\s*(?:[\w<>\[\],\s]+?)\s+([a-z]\w*)\s*\("),
        "import": re.compile(r"\bimport\s+([a-zA-Z_.\d]+)"),
    },
    "c": {
        "function": re.compile(r"\b(?:static\s+|inline\s+|extern\s+)?[\w\s\*]+?\b([a-z_]\w*)\s*\([^;]*\)\s*\{"),
        "type": re.compile(r"\btypedef\s+[\w\s\*]+?\s+([a-z_]\w*)\s*;"),
        "struct": re.compile(r"\bstruct\s+([a-z_]\w*)"),
        "include": re.compile(r"\#include\s*[<\"]([^>\"]+)[>\"]"),
    },
    "cpp": {
        "class": re.compile(r"\bclass\s+([A-Za-z_]\w*)"),
        "struct": re.compile(r"\bstruct\s+([A-Za-z_]\w*)"),
        "function": re.compile(r"\b(?:static\s+|inline\s+|virtual\s+|explicit\s+)?[\w:<>\s\*&]+?\b([a-z_]\w*)\s*\([^;]*\)\s*\{"),
        "include": re.compile(r"\#include\s*[<\"]([^>\"]+)[>\"]"),
    },
    "csharp": {
        "class": re.compile(r"\bclass\s+([A-Z]\w*)"),
        "interface": re.compile(r"\binterface\s+([A-Z]\w*)"),
        "method": re.compile(r"\b(?:public|private|protected|internal|static|async|virtual|override|sealed|partial)?\s*(?:[\w<>\[\],\s]+?)\s+([A-Z]\w*)\s*\("),
        "using": re.compile(r"\busing\s+([\w.]+)"),
    },
    "ruby": {
        "class": re.compile(r"\bclass\s+([A-Z]\w*)"),
        "module": re.compile(r"\bmodule\s+([A-Z]\w*)"),
        "method": re.compile(r"\bdef\s+(?:self\.)?([a-z_]\w*)\s*(?:\(|$)"),
        "require": re.compile(r"\b(?:require|require_relative)\s+['\"]([^'\"]+)['\"]"),
    },
    "php": {
        "class": re.compile(r"\bclass\s+([A-Za-z_]\w*)"),
        "function": re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\("),
        "method": re.compile(r"\b(?:public|private|protected|static)?\s*function\s+([A-Za-z_]\w*)\s*\("),
        "use": re.compile(r"\buse\s+([A-Za-z_\\\w]+)"),
    },
    "shell": {
        "function": re.compile(r"^([A-Za-z_]\w*)\s*\(\s*\)\s*\{", re.MULTILINE),
    },
}

_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")


def parse_other(source: str, rel_path: str, language: str) -> ParseResult:
    """Regex-heuristic parse for non-Python languages. Every node emitted is
    flagged approximate — the graph is honest about extraction confidence."""
    result = ParseResult()
    patterns = _LANG_PATTERNS.get(language)
    if not patterns:
        return result
    module_fq = Path(rel_path).with_suffix("").as_posix().replace("/", ".")

    # A minimal scope stack: top-level names (module) + last class seen.
    class_stack: List[str] = []
    module_fq = module_fq.replace(".", ".")

    def parent_fq() -> str:
        return ".".join(class_stack) if class_stack else module_fq

    seen: set[Tuple[str, str, str]] = set()  # (kind, name, fq) — dedupe by line-cluster

    def add_node(kind: str, name: str, line: int, col: int = 0) -> str:
        fq = f"{parent_fq()}.{name}" if parent_fq() else name
        key = (kind, name, fq)
        if key in seen:
            return fq
        seen.add(key)
        result.nodes.append(
            {
                "kind": kind,
                "name": name,
                "fq_name": fq,
                "file": rel_path,
                "line": line,
                "col": col,
                "signature": "",
                "approximate": True,
            }
        )
        return fq

    def add_edge(relation: str, source: str, target: str, line: int) -> None:
        result.edges.append(
            {
                "relation": relation,
                "source": source,
                "target": target,
                "file": rel_path,
                "line": line,
            }
        )

    lines = source.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        # class/struct/interface/type declarations advance the class stack
        for pat_key in ("class", "struct", "interface", "enum", "impl"):
            pat = patterns.get(pat_key)
            if not pat:
                continue
            m = pat.search(stripped)
            if m:
                name = m.group(1)
                fq = add_node("class" if pat_key in ("class", "struct") else "type", name, idx)
                class_stack.append(fq)
                break

        # imports / use / require / include
        for imp_key in ("import", "use", "require", "using", "include"):
            pat = patterns.get(imp_key)
            if not pat:
                continue
            m = pat.search(stripped)
            if m:
                add_edge("imports", parent_fq(), m.group(1), idx)
                break

        # functions / methods / arrows — a declaration is a match that
        # begins the line (or, for methods, is preceded by at most a few
        # spaces of indentation). Approximate by design.
        for fn_key in ("function", "method", "arrow"):
            pat = patterns.get(fn_key)
            if not pat:
                continue
            m = pat.search(stripped)
            if m is None:
                continue
            indent = len(line) - len(line.lstrip())
            if m.start() != 0 and indent == 0:
                continue  # a mid-line match at column 0 — skip
            name = m.group(1)
            kind = "method" if class_stack else "function"
            add_node(kind, name, idx, col=line.find(name) if name in line else 0)
            break

    # Second pass: link calls to defined symbols (approximate resolution).
    defined_names = {n["name"] for n in result.nodes}
    defined_fqs: Dict[str, str] = {}
    for n in result.nodes:
        defined_fqs.setdefault(n["name"], n["fq_name"])
    for idx, line in enumerate(lines, start=1):
        for m in _CALL_RE.finditer(line):
            name = m.group(1)
            if name in defined_names and defined_fqs[name] != parent_fq():
                add_edge("calls", parent_fq(), defined_fqs[name], idx)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_source(source: str, rel_path: str, language: Optional[str] = None) -> ParseResult:
    """Parse source text for one file. ``language`` defaults to detection
    from the file extension; unknown languages return an empty result."""
    lang = language or language_for(rel_path)
    if lang is None:
        return ParseResult()
    if lang == "python":
        return parse_python(source, rel_path)
    return parse_other(source, rel_path, lang)

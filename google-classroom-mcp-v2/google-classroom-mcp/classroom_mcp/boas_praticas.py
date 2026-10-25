"""Verificacoes objetivas de qualidade (rodam localmente, sem gastar tokens).

Nunca executa o codigo do aluno: so analise estatica (ast, regex e, se instalado, ruff).
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .filetypes import ext_of
from .models import FileItem
from .notebook import strip_magics

LONG_FUNCTION_LINES = 50
HIGH_COMPLEXITY = 10
MAX_FINDINGS_PER_FILE = 12


def _complexity(fn: ast.AST) -> int:
    n = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler,
                             ast.With, ast.AsyncWith, ast.IfExp, ast.comprehension)):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
    return n


def analyze_python_source(code: str) -> dict:
    """Metricas e achados de um trecho de Python."""
    out = {"erro_sintaxe": None, "achados": [], "metricas": {}}
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        out["erro_sintaxe"] = "linha %s: %s" % (e.lineno, e.msg)
        out["achados"].append("erro de sintaxe (%s)" % out["erro_sintaxe"])
        return out

    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    lines = code.splitlines()
    nonblank = [l for l in lines if l.strip()]
    comment_lines = [l for l in nonblank if l.strip().startswith("#")]
    doc_missing, long_fns, complex_fns, bare_except, mutable_defaults = [], [], [], [], []
    for f in funcs:
        end = getattr(f, "end_lineno", f.lineno)
        size = end - f.lineno + 1
        if size > LONG_FUNCTION_LINES:
            long_fns.append("%s (linha %d, %d linhas)" % (f.name, f.lineno, size))
        c = _complexity(f)
        if c > HIGH_COMPLEXITY:
            complex_fns.append("%s (linha %d, complexidade %d)" % (f.name, f.lineno, c))
        if not f.name.startswith("_") and ast.get_docstring(f) is None:
            doc_missing.append(f.name)
        for d in f.args.defaults + [x for x in f.args.kw_defaults if x]:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                mutable_defaults.append("%s (linha %d)" % (f.name, f.lineno))
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler) and n.type is None:
            bare_except.append(n.lineno)

    imported: dict[str, int] = {}
    star = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imported[(a.asname or a.name).split(".")[0]] = n.lineno
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "*":
                    star.append(n.lineno)
                else:
                    imported[a.asname or a.name] = n.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
           {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    unused = [k for k in imported if k not in used and k != "*"]

    one_letter = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                         and len(n.id) == 1 and n.id not in "ijkxyzn_eEfc"})
    long_lines = sum(1 for l in lines if len(l) > 100)
    prints = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print")

    m = out["metricas"]
    m.update({"linhas": len(nonblank), "funcoes": len(funcs), "classes": len(classes),
              "comentarios_pct": round(100 * len(comment_lines) / max(1, len(nonblank))),
              "funcoes_com_docstring_pct": round(100 * (1 - len(doc_missing) / max(1, len([f for f in funcs if not f.name.startswith('_')])))) if funcs else None,
              "prints": prints})

    a = out["achados"]
    if long_fns:
        a.append("funcoes longas (>%d linhas): %s" % (LONG_FUNCTION_LINES, "; ".join(long_fns[:4])))
    if complex_fns:
        a.append("complexidade alta: %s" % "; ".join(complex_fns[:4]))
    if bare_except:
        a.append("except sem tipo (linhas %s)" % ", ".join(map(str, bare_except[:5])))
    if mutable_defaults:
        a.append("argumento padrao mutavel: %s" % "; ".join(mutable_defaults[:3]))
    if star:
        a.append("import * (linhas %s)" % ", ".join(map(str, star[:3])))
    if unused:
        a.append("imports nao usados: %s" % ", ".join(sorted(unused)[:8]))
    if one_letter:
        a.append("variaveis de uma letra pouco descritivas: %s" % ", ".join(one_letter[:8]))
    if long_lines:
        a.append("%d linha(s) com mais de 100 caracteres" % long_lines)
    if funcs and not doc_missing and len(funcs) >= 4:
        pass  # sinal tratado em sinais.py
    elif doc_missing and len(doc_missing) >= max(2, len(funcs) // 2):
        a.append("funcoes publicas sem docstring: %s" % ", ".join(doc_missing[:6]))
    if len(nonblank) > 30 and not comment_lines and not funcs:
        a.append("codigo longo sem funcoes e sem comentarios (script linear)")
    return out


def ruff_check(code: str, timeout: int = 20) -> dict | None:
    """Roda o ruff (se instalado) sobre o codigo. So analise estatica."""
    exe = shutil.which("ruff")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "aluno.py"
        p.write_text(code, encoding="utf-8")
        try:
            r = subprocess.run(
                [exe, "check", "--no-cache", "--isolated", "--output-format=json",
                 "--select", "E,F,W,B,SIM", "--ignore", "E501,W291,W293", str(p)],
                capture_output=True, text=True, timeout=timeout)
            data = json.loads(r.stdout or "[]")
        except Exception:
            return None
    counts: dict[str, int] = {}
    examples = []
    for item in data:
        counts[item["code"]] = counts.get(item["code"], 0) + 1
        if len(examples) < 5:
            examples.append("%s linha %s: %s" % (item["code"], item["location"]["row"], item["message"]))
    return {"total": len(data), "por_regra": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8]), "exemplos": examples}


def analyze_html(text: str) -> list[str]:
    low = text.lower()
    a = []
    if "<!doctype" not in low[:300]:
        a.append("sem <!DOCTYPE html>")
    if "<html" in low and not re.search(r"<html[^>]*\blang=", low):
        a.append("<html> sem atributo lang")
    if "<title" not in low:
        a.append("sem <title>")
    if "<meta charset" not in low and "charset=" not in low:
        a.append("sem meta charset")
    imgs = re.findall(r"<img\b[^>]*>", low)
    sem_alt = [i for i in imgs if "alt=" not in i]
    if sem_alt:
        a.append("%d imagem(ns) sem atributo alt (acessibilidade)" % len(sem_alt))
    if re.search(r"<(font|center|marquee|blink)\b", low):
        a.append("tags obsoletas (font/center/marquee)")
    inline = len(re.findall(r"\sstyle=", low))
    if inline > 5:
        a.append("%d estilos inline (prefira CSS separado)" % inline)
    return a


def analyze_js(text: str) -> list[str]:
    a = []
    var_n = len(re.findall(r"(?m)^\s*var\s", text))
    if var_n:
        a.append("%d uso(s) de var (prefira let/const)" % var_n)
    loose = len(re.findall(r"[^=!<>]==[^=]|!=[^=]", text))
    if loose:
        a.append("%d comparacao(oes) com == ou != (prefira === e !==)" % loose)
    logs = len(re.findall(r"console\.log\(", text))
    if logs > 5:
        a.append("%d console.log esquecidos" % logs)
    if re.search(r"\beval\(|document\.write\(", text):
        a.append("uso de eval() ou document.write()")
    return a


def analyze_item(item: FileItem, use_ruff: bool = True) -> dict | None:
    """Analise de um FileItem. Retorna None se nao houver o que verificar."""
    if item.error or not item.text:
        return None
    ext = ext_of(item.name.split("::")[-1])
    res = {"arquivo": item.name, "linguagem": "", "achados": [], "metricas": {}, "ruff": None}

    if item.kind == "notebook":
        res["linguagem"] = "notebook"
        stats = item.meta.get("stats", {})
        cells = item.meta.get("code_cells", [])
        for i, c in enumerate(cells, 1):
            r = analyze_python_source(strip_magics(c))
            if r["erro_sintaxe"]:
                res["achados"].append("celula de codigo %d: erro de sintaxe (%s)" % (i, r["erro_sintaxe"]))
        joined = strip_magics("\n\n".join(cells))
        r = analyze_python_source(joined)
        if not r["erro_sintaxe"]:
            res["achados"] += r["achados"]
            res["metricas"] = r["metricas"]
        res["metricas"].update({"celulas": stats.get("celulas"), "markdown": stats.get("markdown"),
                                "palavras_markdown": stats.get("palavras_markdown")})
        res["achados"] += item.warnings  # execucao, erros salvos, ordem
        if stats.get("markdown", 0) == 0 and stats.get("codigo", 0) > 3:
            res["achados"].append("notebook sem nenhuma celula de texto/markdown (sem explicacao)")
        if use_ruff and not r["erro_sintaxe"]:
            res["ruff"] = ruff_check(joined)
    elif ext == ".py":
        res["linguagem"] = "python"
        r = analyze_python_source(item.text)
        res["achados"], res["metricas"] = r["achados"], r["metricas"]
        if use_ruff and not r["erro_sintaxe"]:
            res["ruff"] = ruff_check(item.text)
    elif ext in (".html", ".htm"):
        res["linguagem"] = "html"
        res["achados"] = analyze_html(item.text)
    elif ext in (".js", ".jsx", ".ts", ".tsx"):
        res["linguagem"] = "javascript/typescript"
        res["achados"] = analyze_js(item.text)
    else:
        return None
    res["achados"] = res["achados"][:MAX_FINDINGS_PER_FILE]
    return res


async def analyze_items(items: list[FileItem]) -> list[dict]:
    out = []
    for it in items:
        r = await asyncio.to_thread(analyze_item, it)
        if r:
            out.append(r)
    return out


def format_analysis(results: list[dict]) -> str:
    if not results:
        return "Nenhum arquivo de codigo analisavel (Python, notebook, HTML, JS)."
    L = []
    for r in results:
        m = r["metricas"]
        met = ", ".join("%s=%s" % (k, v) for k, v in m.items() if v is not None)
        L.append("%s [%s]%s" % (r["arquivo"], r["linguagem"], (" " + met) if met else ""))
        for a in r["achados"]:
            L.append("  - " + a)
        if r["ruff"]:
            rf = r["ruff"]
            if rf["total"]:
                L.append("  - ruff: %d aviso(s) (%s)" % (rf["total"], ", ".join("%s x%d" % kv for kv in rf["por_regra"].items())))
                L += ["      " + e for e in rf["exemplos"][:3]]
            else:
                L.append("  - ruff: sem avisos")
        if not r["achados"] and not (r["ruff"] and r["ruff"]["total"]):
            L.append("  - nenhum problema objetivo encontrado")
    return "\n".join(L)

"""Leitura de notebooks Jupyter/Colab (.ipynb)."""

from __future__ import annotations

import json
import re

from .config import MAX_NOTEBOOK_OUTPUT_CHARS


def _src(cell: dict) -> str:
    s = cell.get("source", "")
    return "".join(s) if isinstance(s, list) else (s or "")


def _clip(text: str, limit: int = MAX_NOTEBOOK_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[... saida cortada, %d caracteres omitidos]" % (len(text) - limit)


def _output_text(out: dict) -> tuple[str, bool, bool]:
    """Retorna (texto, e_erro, tinha_imagem)."""
    otype = out.get("output_type", "")
    if otype == "stream":
        t = out.get("text", "")
        return (_clip("".join(t) if isinstance(t, list) else t), False, False)
    if otype == "error":
        return ("%s: %s" % (out.get("ename", "Erro"), out.get("evalue", "")), True, False)
    if otype in ("execute_result", "display_data"):
        data = out.get("data", {})
        has_img = any(k.startswith("image/") for k in data)
        plain = data.get("text/plain", "")
        plain = "".join(plain) if isinstance(plain, list) else plain
        extra = " [grafico/imagem omitido]" if has_img else ""
        if not plain and "text/html" in data:
            plain = "[tabela HTML omitida]"
        return (_clip(plain) + extra, False, has_img)
    return ("", False, False)


MAGIC_LINE = re.compile(r"^\s*[%!?]")


def strip_magics(code: str) -> str:
    """Remove magics (%, !) e ajuda (?) para permitir analise com ast."""
    return "\n".join("# " + l if MAGIC_LINE.match(l) else l for l in code.splitlines())


def parse_notebook(raw: str | bytes) -> dict:
    """Converte um .ipynb em texto legivel, codigo puro e estatisticas."""
    nb = json.loads(raw)
    cells = nb.get("cells")
    if cells is None:  # nbformat 3
        cells = [c for ws in nb.get("worksheets", []) for c in ws.get("cells", [])]

    parts: list[str] = []
    code_cells: list[str] = []
    stats = {
        "celulas": len(cells), "codigo": 0, "markdown": 0, "codigo_vazio": 0,
        "nao_executadas": 0, "com_erro": [], "fora_de_ordem": False,
        "imagens_nas_saidas": 0, "palavras_markdown": 0, "sem_saidas": 0,
    }
    last_exec = 0
    executed_any = False

    for i, c in enumerate(cells, 1):
        ctype = c.get("cell_type", "code")
        src = _src(c)
        if ctype == "markdown":
            stats["markdown"] += 1
            stats["palavras_markdown"] += len(src.split())
            parts.append("# --- celula %d [markdown] ---\n%s" % (i, src))
        elif ctype == "code":
            stats["codigo"] += 1
            ec = c.get("execution_count")
            if not src.strip():
                stats["codigo_vazio"] += 1
                continue
            code_cells.append(src)
            if ec is None:
                stats["nao_executadas"] += 1
            else:
                executed_any = True
                if ec < last_exec:
                    stats["fora_de_ordem"] = True
                last_exec = max(last_exec, ec)
            head = "# --- celula %d [codigo]%s ---" % (i, " (execucao %s)" % ec if ec is not None else " (nao executada)")
            block = [head, src]
            outs = c.get("outputs", [])
            if not outs and ec is not None:
                stats["sem_saidas"] += 1
            out_lines: list[str] = []
            for out in outs:
                t, is_err, has_img = _output_text(out)
                if is_err:
                    stats["com_erro"].append("celula %d: %s" % (i, t))
                if has_img:
                    stats["imagens_nas_saidas"] += 1
                if t:
                    out_lines.append(t)
            if out_lines:
                block.append("# saida:\n" + "\n".join(out_lines))
            parts.append("\n".join(block))
        elif ctype == "raw" and src.strip():
            parts.append("# --- celula %d [raw] ---\n%s" % (i, src))

    stats["algum_executada"] = executed_any
    return {
        "text": "\n\n".join(parts),
        "code": "\n\n".join(strip_magics(c) for c in code_cells),
        "code_cells": code_cells,
        "stats": stats,
    }


def notebook_warnings(stats: dict) -> list[str]:
    w = []
    if stats["com_erro"]:
        w.append("%d celula(s) com erro salvo na saida (%s)" % (len(stats["com_erro"]), stats["com_erro"][0]))
    if stats["fora_de_ordem"]:
        w.append("celulas executadas fora de ordem (pode nao rodar do inicio ao fim)")
    if stats["codigo"] and stats["nao_executadas"] == stats["codigo"] - stats["codigo_vazio"] and stats["codigo"] > 0:
        w.append("nenhuma celula de codigo foi executada (sem saidas salvas)")
    elif stats["nao_executadas"]:
        w.append("%d celula(s) de codigo nao executada(s)" % stats["nao_executadas"])
    return w

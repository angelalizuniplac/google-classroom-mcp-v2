"""Deteccao de similaridade entre entregas.

Metodo (inspirado no MOSS):
  1. normaliza o conteudo (remove comentarios; em codigo, tambem renomeia identificadores);
  2. gera impressoes digitais por trechos (k-gramas de tokens + winnowing);
  3. remove o que e comum a todos (enunciado, codigo inicial do professor, boilerplate);
  4. compara por "contencao" (quanto do menor trabalho aparece no outro), por arquivo e por aluno;
  5. mostra trechos coincidentes lado a lado como evidencia.

E uma ferramenta de triagem. A decisao e sempre da professora.
"""

from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations

from .filetypes import ext_of
from .models import FileItem

KEYWORDS = set("""
and as assert async await break class continue def del elif else except finally for from global if import in is
lambda none nonlocal not or pass raise return try while with yield true false self print
abstract boolean byte case catch char const default do double enum extends final float goto implements instanceof
int interface long native new null package private protected public short static super switch synchronized this
throw throws transient void volatile var let function typeof undefined export delete of
select insert update where join group order by having create table values into
""".split())

TOKEN_RE = re.compile(r"[^\W\d]\w*|\d+(?:\.\d+)?|\"[^\"\n]*\"|'[^'\n]*'|\S", re.UNICODE)
IDENT_RE = re.compile(r"^[^\W\d]\w*$", re.UNICODE)

K_CODE_LIT, K_CODE_EST, K_PROSE, WINDOW = 6, 10, 5, 4
MIN_FINGERPRINTS = 15         # abaixo disso a comparacao e inconclusiva
TRIVIAL_LINE_LEN = 10
MAX_LINES_FOR_EVIDENCE = 4000


# --------------------------------------------------------------------------
# Normalizacao
# --------------------------------------------------------------------------

def strip_comments(text: str, ext: str) -> str:
    if ext in (".py", ".ipynb", ".sh", ".bash", ".r", ".rb", ".ps1", ".yaml", ".yml"):
        return re.sub(r"(?m)#.*$", "", text)
    if ext in (".html", ".htm", ".xml", ".vue"):
        text = re.sub(r"(?s)<!--.*?-->", "", text)
        text = re.sub(r"(?s)/\*.*?\*/", "", text)
        return re.sub(r"(?m)(?<![:\"'])//.*$", "", text)
    if ext == ".sql":
        return re.sub(r"(?m)--.*$", "", re.sub(r"(?s)/\*.*?\*/", "", text))
    # estilo C (js, ts, java, c, cpp, cs, php, go, rs, kt, swift, css, ...)
    text = re.sub(r"(?s)/\*.*?\*/", "", text)
    return re.sub(r"(?m)(?<![:\"'])//.*$", "", text)


def tokenize(text: str, rename: bool = False, lower: bool = True) -> list[str]:
    toks = TOKEN_RE.findall(text.lower() if lower else text)
    if not rename:
        return toks
    return ["ID" if (IDENT_RE.match(t) and t not in KEYWORDS) else t for t in toks]


def norm_line(line: str, ext: str) -> str:
    return " ".join(tokenize(strip_comments(line, ext), rename=True))


def winnow(tokens: list[str], k: int, w: int = WINDOW) -> set[int]:
    if len(tokens) < k:
        return set()
    hashes = [zlib.crc32(" ".join(tokens[i:i + k]).encode("utf-8")) for i in range(len(tokens) - k + 1)]
    if len(hashes) <= w:
        return {min(hashes)}
    return {min(hashes[i:i + w]) for i in range(len(hashes) - w + 1)}


def containment(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# --------------------------------------------------------------------------
# Documentos
# --------------------------------------------------------------------------

@dataclass
class Doc:
    student: str
    name: str
    kind: str                      # code | prose
    ext: str
    lit: set = field(default_factory=set)
    est: set = field(default_factory=set)
    lines: list = field(default_factory=list)   # [(nº, original, normalizada)]


def build_doc(student: str, name: str, text: str, kind: str, is_notebook: bool = False) -> Doc | None:
    if kind not in ("code", "prose") or not text or not text.strip():
        return None
    ext = ".py" if is_notebook else ext_of(name.split("::")[-1])
    doc = Doc(student=student, name=name, kind=kind, ext=ext)
    if kind == "code":
        stripped = strip_comments(text, ext)
        doc.lit = winnow(tokenize(stripped), K_CODE_LIT)
        doc.est = winnow(tokenize(stripped, rename=True), K_CODE_EST)
        for i, line in enumerate(text.splitlines(), 1):
            n = norm_line(line, ext)
            doc.lines.append((i, line, n))
    else:
        doc.lit = winnow(tokenize(text), K_PROSE)
        sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
        for i, s in enumerate(sentences, 1):
            n = " ".join(tokenize(s))
            doc.lines.append((i, s.strip(), n))
    return doc if (doc.lit or doc.est) else None


def docs_from_items(student: str, items: list[FileItem]) -> list[Doc]:
    out = []
    for it in items:
        d = build_doc(student, it.name, it.plag_text, it.plag_kind, is_notebook=(it.kind == "notebook"))
        if d:
            out.append(d)
    return out


# --------------------------------------------------------------------------
# Analise
# --------------------------------------------------------------------------

def _common_threshold(n_students: int) -> int:
    return max(4, math.ceil(0.4 * n_students))


def _evidence(a: Doc, b: Doc, ignore_lines: set[str], max_blocks: int = 3) -> list[dict]:
    la = a.lines[:MAX_LINES_FOR_EVIDENCE]
    lb = b.lines[:MAX_LINES_FOR_EVIDENCE]
    sm = SequenceMatcher(None, [x[2] for x in la], [x[2] for x in lb], autojunk=False)
    blocks = []
    for m in sm.get_matching_blocks():
        if m.size == 0:
            continue
        seg = [la[m.a + i] for i in range(m.size)]
        useful = [s for s in seg if len(s[2]) >= TRIVIAL_LINE_LEN and s[2] not in ignore_lines]
        chars = sum(len(s[2]) for s in useful)
        min_lines = 2 if a.kind == "code" else 1
        if len(useful) >= min_lines and chars >= 60:
            blocks.append((chars, m, seg))
    blocks.sort(key=lambda x: -x[0])
    out = []
    for chars, m, seg in blocks[:max_blocks]:
        excerpt = "\n".join(s[1].rstrip() for s in seg[:8])
        if len(seg) > 8:
            excerpt += "\n... (+%d linhas)" % (len(seg) - 8)
        out.append({
            "a_linhas": "%d-%d" % (la[m.a][0], la[m.a + m.size - 1][0]),
            "b_linhas": "%d-%d" % (lb[m.b][0], lb[m.b + m.size - 1][0]),
            "trecho": excerpt, "caracteres": chars,
        })
    return out


def analyze(students: dict[str, list[Doc]], baseline: list[Doc], limiar: float = 0.7,
            dupla_pairs: set[frozenset] | None = None, history: list[dict] | None = None) -> dict:
    """students: nome -> Docs. baseline: Docs do professor (enunciado, codigo inicial)."""
    dupla_pairs = dupla_pairs or set()
    names = list(students)
    n = len(names)

    base_lit = set().union(*[d.lit for d in baseline]) if baseline else set()
    base_est = set().union(*[d.est for d in baseline]) if baseline else set()
    base_lines = {l[2] for d in baseline for l in d.lines}

    # Hashes comuns a muitos alunos (boilerplate)
    freq_lit: dict[int, int] = {}
    freq_est: dict[int, int] = {}
    for name in names:
        s_lit = set().union(*[d.lit for d in students[name]]) if students[name] else set()
        s_est = set().union(*[d.est for d in students[name]]) if students[name] else set()
        for h in s_lit:
            freq_lit[h] = freq_lit.get(h, 0) + 1
        for h in s_est:
            freq_est[h] = freq_est.get(h, 0) + 1
    thr = _common_threshold(n) if n >= 5 else 10 ** 9
    common_lit = {h for h, c in freq_lit.items() if c >= thr}
    common_est = {h for h, c in freq_est.items() if c >= thr}
    drop_lit, drop_est = base_lit | common_lit, base_est | common_est

    # Conjuntos filtrados por documento e por aluno
    for name in names:
        for d in students[name]:
            d.lit = d.lit - drop_lit
            d.est = d.est - drop_est

    def agg(name: str, kind: str):
        docs = [d for d in students[name] if d.kind == kind]
        return (set().union(*[d.lit for d in docs]) if docs else set(),
                set().union(*[d.est for d in docs]) if docs else set())

    agg_cache = {(nm, k): agg(nm, k) for nm in names for k in ("code", "prose")}

    pairs, inconclusive = [], set()
    for a, b in combinations(names, 2):
        best = None
        for kind in ("code", "prose"):
            la, ea = agg_cache[(a, kind)]
            lb, eb = agg_cache[(b, kind)]
            if not la or not lb:
                continue
            if min(len(la), len(lb)) < MIN_FINGERPRINTS:
                inconclusive.update([a, b] if False else [x for x, s in ((a, la), (b, lb)) if len(s) < MIN_FINGERPRINTS])
                continue
            lit = containment(la, lb)
            est = containment(ea, eb) if kind == "code" else 0.0
            score = max(lit, est - 0.1)
            cand = {"tipo": kind, "literal": lit, "estrutural": est, "score": score}
            if best is None or cand["score"] > best["score"]:
                best = cand
        if best is None:
            continue
        is_dupla = frozenset((a, b)) in dupla_pairs
        pair = {"a": a, "b": b, "dupla": is_dupla, **best, "evidencias": [], "arquivo_a": "", "arquivo_b": ""}
        pairs.append(pair)

    pairs.sort(key=lambda p: -p["score"])

    # Evidencias apenas para os pares relevantes (custo)
    show_from = max(0.35, limiar * 0.7)
    for p in pairs:
        if p["score"] < show_from:
            continue
        docs_a = [d for d in students[p["a"]] if d.kind == p["tipo"]]
        docs_b = [d for d in students[p["b"]] if d.kind == p["tipo"]]
        best_pair, best_val = None, 0.0
        for da in docs_a:
            for db in docs_b:
                v = max(containment(da.lit, db.lit), containment(da.est, db.est) - 0.1 if p["tipo"] == "code" else 0)
                if min(len(da.lit), len(db.lit)) >= 5 and v > best_val:
                    best_pair, best_val = (da, db), v
        if best_pair:
            p["arquivo_a"], p["arquivo_b"] = best_pair[0].name, best_pair[1].name
            p["score_arquivo"] = best_val
            p["evidencias"] = _evidence(best_pair[0], best_pair[1], base_lines)

    suspicious = [p for p in pairs if p["score"] >= limiar and not p["dupla"]]
    duplas = [p for p in pairs if p["dupla"]]

    # Fingerprints completos (antes do filtro) nao sao guardados; guardamos os filtrados
    fingerprints = {}
    for nm in names:
        fp = {}
        for kind in ("code", "prose"):
            lit, est = agg_cache[(nm, kind)]
            if lit:
                fp[kind] = {"lit": sorted(lit), "est": sorted(est)}
        if fp:
            fingerprints[nm] = fp

    # Historico (outras turmas, semestres anteriores)
    hist_hits = []
    for row in history or []:
        for nm in names:
            cur = agg_cache[(nm, row["kind"])]
            if not cur[0] or min(len(cur[0]), len(row["lit"])) < MIN_FINGERPRINTS:
                continue
            lit = containment(cur[0], set(row["lit"]))
            est = containment(cur[1], set(row["est"])) if row["kind"] == "code" else 0.0
            score = max(lit, est - 0.1)
            if score >= limiar:
                hist_hits.append({"aluno": nm, "score": score, "origem": row["label"], "aluno_origem": row["name"]})
    hist_hits.sort(key=lambda h: -h["score"])

    return {
        "alunos": n, "pares": pairs, "suspeitos": suspicious, "duplas": duplas,
        "inconclusivos": sorted(inconclusive), "sem_conteudo": [nm for nm in names if not students[nm]],
        "removidos_baseline": len(base_lit), "removidos_comuns": len(common_lit),
        "historico": hist_hits, "fingerprints": fingerprints, "limiar": limiar,
    }


def format_report(r: dict, top: int = 25) -> str:
    lim = r["limiar"]
    L = [
        "RELATORIO DE SIMILARIDADE",
        "Alunos comparados : %d" % r["alunos"],
        "Pares comparados  : %d" % len(r["pares"]),
        "Limiar de alerta  : %.0f%% (do trabalho menor que aparece no outro)" % (lim * 100),
        "Pares suspeitos   : %d" % len(r["suspeitos"]),
        "Duplas declaradas : %d (nao contam como suspeitas)" % len(r["duplas"]),
        "Material comum removido: %d trechos do enunciado/codigo inicial e %d trechos comuns a muitos alunos"
        % (r["removidos_baseline"], r["removidos_comuns"]),
        "",
    ]
    if r["suspeitos"]:
        L.append("PARES SUSPEITOS (do mais para o menos similar)")
        for p in r["suspeitos"]:
            L.append("* %s  x  %s  [%s]" % (p["a"], p["b"], "codigo" if p["tipo"] == "code" else "texto"))
            L.append("    similaridade: %.0f%% (literal %.0f%%, estrutural com identificadores renomeados %.0f%%)"
                     % (p["score"] * 100, p["literal"] * 100, p["estrutural"] * 100))
            if p["arquivo_a"]:
                L.append("    arquivos: %s  |  %s" % (p["arquivo_a"], p["arquivo_b"]))
            for ev in p["evidencias"]:
                L.append("    trecho em comum (A linhas %s, B linhas %s):" % (ev["a_linhas"], ev["b_linhas"]))
                L += ["        " + ln for ln in ev["trecho"].splitlines()]
        L.append("")
    if r["duplas"]:
        L.append("DUPLAS DECLARADAS")
        for p in r["duplas"]:
            L.append("  %s  +  %s : %.0f%%" % (p["a"], p["b"], p["score"] * 100))
        L.append("")
    if r["historico"]:
        L.append("SEMELHANCA COM TRABALHOS GUARDADOS DE OUTRAS ATIVIDADES/TURMAS")
        for h in r["historico"][:15]:
            L.append("  %s  ~  %s (%s): %.0f%%" % (h["aluno"], h["aluno_origem"], h["origem"], h["score"] * 100))
        L.append("")
    L.append("RANKING (top %d)" % top)
    for p in r["pares"][:top]:
        flag = "ALERTA" if (p["score"] >= lim and not p["dupla"]) else ("dupla" if p["dupla"] else ("atencao" if p["score"] >= 0.4 else "ok"))
        L.append("  %5.1f%%  %-8s %s  x  %s" % (p["score"] * 100, flag, p["a"], p["b"]))
    if r["inconclusivos"]:
        L += ["", "INCONCLUSIVOS (pouco conteudo para comparar): " + ", ".join(r["inconclusivos"])]
    if r["sem_conteudo"]:
        L += ["SEM CONTEUDO LEGIVEL (nao entraram na comparacao): " + ", ".join(r["sem_conteudo"])]
    L += [
        "",
        "Como ler: similaridade alta indica trechos iguais, nao prova copia. Trabalhos sobre o mesmo tema ou",
        "com a mesma solucao classica podem coincidir. Confira os trechos e converse com os alunos antes de decidir.",
    ]
    return "\n".join(L)

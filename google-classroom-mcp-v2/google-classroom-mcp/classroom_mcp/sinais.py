"""Sinais de autoria: evidencias verificaveis, nao um percentual de 'IA'.

Nenhum destes sinais prova autoria. Servem para decidir quem merece uma conversa
(arguicao oral) e dao material concreto para essa conversa.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone

from .classroom_api import run
from .filetypes import ext_of
from .github_reader import get_commits
from .models import AttachmentResult, FileItem
from .notebook import strip_magics

GENERIC_COMMITS = re.compile(
    r"^(add files via upload|first commit|initial commit|update|updated|commit|upload|final|ok|teste?|atividade|trabalho)\b",
    re.IGNORECASE)

LLM_PHRASES = re.compile(
    r"(aqui est[aá]|here(?:'s| is)|certamente[!,.]|claro[!,]|como (?:um )?modelo de (?:ia|linguagem)|as an ai|"
    r"espero que isso ajude|i hope this helps|vamos (?:l[aá]|come[cç]ar)|let'?s (?:dive|get started)|\bstep \d+[:.]|"
    r"passo \d+[:.]|^#+\s*(?:resumo|conclus[aã]o|introdu[cç][aã]o)\b)", re.IGNORECASE | re.MULTILINE)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s)
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def due_datetime(work: dict) -> datetime | None:
    d, t = work.get("dueDate"), work.get("dueTime") or {}
    if not d:
        return None
    try:
        return datetime(d["year"], d["month"], d["day"], t.get("hours", 23), t.get("minutes", 59), tzinfo=timezone.utc)
    except Exception:
        return None


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600


def entrega_signals(sub: dict, work: dict) -> list[dict]:
    out = []
    due = due_datetime(work)
    history = (sub.get("submissionHistory") or [])
    turned = [parse_ts(h["stateHistory"]["stateTimestamp"]) for h in history
              if h.get("stateHistory", {}).get("state") == "TURNED_IN" and h["stateHistory"].get("stateTimestamp")]
    turned = [t for t in turned if t]
    reclaims = sum(1 for h in history if h.get("stateHistory", {}).get("state") == "RECLAIMED_BY_STUDENT")
    last_turn = max(turned) if turned else parse_ts(sub.get("updateTime"))
    if sub.get("late"):
        out.append({"nivel": "info", "texto": "entrega marcada como atrasada pelo Classroom"})
    if due and last_turn:
        delta = due - last_turn
        if timedelta(0) <= delta <= timedelta(hours=1):
            out.append({"nivel": "info", "texto": "entregue %d min antes do prazo" % (delta.total_seconds() // 60)})
    if reclaims:
        out.append({"nivel": "info", "texto": "aluno cancelou e reenviou a entrega %d vez(es)" % reclaims})
    return out


async def drive_signals(ds, att: AttachmentResult, student_email: str, turned_in: datetime | None) -> list[dict]:
    out = []
    fid = att.meta.get("drive_id")
    if not fid:
        return out
    label = att.title
    try:
        res = await run(ds.revisions().list(
            fileId=fid, pageSize=200,
            fields="revisions(id,modifiedTime,lastModifyingUser(displayName,emailAddress))").execute)
        revs = res.get("revisions", [])
    except Exception as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        why = ("sem permissao para ver o historico de revisoes (o Drive so permite a quem pode editar o arquivo)"
               if status in (403, 404) else "erro: %s" % e)
        return [{"nivel": "info", "texto": "[%s] historico de revisoes indisponivel: %s" % (label, why)}]

    times = sorted(t for t in (parse_ts(r.get("modifiedTime")) for r in revs) if t)
    if not times:
        return [{"nivel": "info", "texto": "[%s] sem revisoes registradas" % label}]
    span = times[-1] - times[0]
    editors = {(r.get("lastModifyingUser") or {}).get("emailAddress", "") for r in revs} - {""}
    txt = "[%s] %d revisao(oes), de %s a %s (%.1f h de duracao)" % (
        label, len(times), times[0].strftime("%d/%m %H:%M"), times[-1].strftime("%d/%m %H:%M"), _hours(span))
    out.append({"nivel": "info", "texto": txt})
    if len(times) <= 2 or _hours(span) < 0.25:
        out.append({"nivel": "atencao", "texto": "[%s] arquivo praticamente sem evolucao: %d revisao(oes) em %.0f min (conteudo pode ter sido colado de uma vez)" % (
            label, len(times), span.total_seconds() / 60)})
    elif turned_in:
        last_hour = sum(1 for t in times if timedelta(0) <= turned_in - t <= timedelta(hours=1))
        if len(times) >= 4 and last_hour / len(times) >= 0.8:
            out.append({"nivel": "info", "texto": "[%s] %d%% das revisoes na ultima hora antes da entrega" % (label, 100 * last_hour // len(times))})
    if student_email and editors and student_email.lower() not in {e.lower() for e in editors}:
        out.append({"nivel": "atencao", "texto": "[%s] nenhuma revisao feita pela conta do aluno (editores: %s)" % (label, ", ".join(sorted(editors)))})
    elif len(editors) > 1:
        out.append({"nivel": "info", "texto": "[%s] editado por %d contas diferentes (pode ser dupla)" % (label, len(editors))})
    return out


async def github_signals(att: AttachmentResult, token: str | None, due: datetime | None, turned_in: datetime | None) -> list[dict]:
    gh = att.meta.get("github")
    if not gh or not gh.get("owner"):
        return []
    commits = await get_commits(gh["owner"], gh["repo"], gh["ref"], token)
    label = "%s/%s" % (gh["owner"], gh["repo"])
    if not commits:
        return [{"nivel": "info", "texto": "[%s] commits indisponiveis (repositorio privado sem token ou limite da API)" % label}]
    dates = sorted(t for t in (parse_ts(c["date"]) for c in commits) if t)
    out = [{"nivel": "info", "texto": "[%s] %d commit(s), de %s a %s" % (
        label, len(commits), dates[0].strftime("%d/%m %H:%M"), dates[-1].strftime("%d/%m %H:%M"))}]
    if len(commits) == 1:
        out.append({"nivel": "atencao", "texto": "[%s] um unico commit com todo o trabalho (sem historico de desenvolvimento)" % label})
    elif _hours(dates[-1] - dates[0]) < 1:
        out.append({"nivel": "atencao", "texto": "[%s] todos os commits em menos de 1 hora" % label})
    generic = [c for c in commits if GENERIC_COMMITS.match(c["mensagem"] or "")]
    if generic and len(generic) / len(commits) >= 0.8 and len(commits) >= 2:
        out.append({"nivel": "info", "texto": "[%s] mensagens de commit genericas (%s...)" % (label, generic[0]["mensagem"])})
    if any(c["mensagem"].lower().startswith("add files via upload") for c in commits):
        out.append({"nivel": "info", "texto": "[%s] arquivos enviados pelo botao de upload do GitHub (nao por git)" % label})
    ref_time = turned_in or due
    if ref_time:
        after = [c for c in commits if (parse_ts(c["date"]) or ref_time) > ref_time + timedelta(minutes=5)]
        if after:
            out.append({"nivel": "atencao", "texto": "[%s] %d commit(s) depois da entrega no Classroom (conteudo mudou apos entregar)" % (label, len(after))})
    authors = {c["autor"] for c in commits if c["autor"]}
    if len(authors) > 1:
        out.append({"nivel": "info", "texto": "[%s] %d autores nos commits: %s" % (label, len(authors), ", ".join(sorted(authors)[:4]))})
    return out


def content_signals(items: list[FileItem]) -> list[dict]:
    out = []
    for it in items:
        if it.error or not it.text or it.kind not in ("code", "notebook", "text", "docx", "pdf"):
            continue
        ext = ext_of(it.name.split("::")[-1])
        name = it.name
        body = it.plag_text or it.text
        phrases = LLM_PHRASES.findall(body)
        if len(phrases) >= 2:
            out.append({"nivel": "atencao", "texto": "[%s] %d expressoes tipicas de texto gerado por chatbot (ex.: \"%s\")" % (name, len(phrases), str(phrases[0])[:40])})
        emojis = len(EMOJI.findall(body))
        if emojis >= 5 and it.kind in ("code", "notebook"):
            out.append({"nivel": "info", "texto": "[%s] %d emojis no codigo/saidas" % (name, emojis)})
        if it.kind == "code" and ext == ".py" or it.kind == "notebook":
            code = strip_magics(body)
            try:
                tree = ast.parse(code)
            except SyntaxError:
                continue
            funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            lines = [l for l in code.splitlines() if l.strip()]
            comments = [l for l in lines if l.strip().startswith("#")]
            if len(funcs) >= 4:
                with_doc = sum(1 for f in funcs if ast.get_docstring(f))
                if with_doc / len(funcs) >= 0.9:
                    out.append({"nivel": "info", "texto": "[%s] %d%% das funcoes com docstring completa" % (name, 100 * with_doc // len(funcs))})
            if len(lines) >= 30 and len(comments) / len(lines) >= 0.3:
                out.append({"nivel": "info", "texto": "[%s] comentarios em %d%% das linhas (muito didatico)" % (name, 100 * len(comments) // len(lines))})
            numbered = sum(1 for l in comments if re.match(r"#\s*(step|passo|etapa)?\s*\d+[.:)]", l.strip(), re.IGNORECASE))
            if numbered >= 4:
                out.append({"nivel": "info", "texto": "[%s] %d comentarios numerados no estilo passo a passo" % (name, numbered)})
    return out


def oral_questions(items: list[FileItem], max_q: int = 4) -> list[str]:
    """Pontos do proprio codigo do aluno para perguntar em uma conversa de verificacao."""
    cands = []
    for it in items:
        if it.error or not it.text:
            continue
        ext = ext_of(it.name.split("::")[-1])
        if it.kind == "notebook" or ext == ".py":
            try:
                tree = ast.parse(strip_magics(it.plag_text or it.text))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    size = getattr(n, "end_lineno", n.lineno) - n.lineno
                    cands.append((size, it.name, n.name, n.lineno))
    cands.sort(reverse=True)
    qs = []
    for size, fname, name, line in cands[:max_q]:
        qs.append("Em %s, explique o que `%s` (linha %d) faz, por que voce a escreveu assim e o que mudaria se o requisito fosse diferente." % (fname, name, line))
    if not qs:
        qs.append("Peca ao aluno para explicar, sem consultar, a parte principal do trabalho e alterar um requisito ao vivo.")
    return qs


async def collect(ds, sub: dict, work: dict, attachments: list[AttachmentResult], student_email: str,
                  github_token: str | None) -> dict:
    due = due_datetime(work)
    history = sub.get("submissionHistory") or []
    turned = [parse_ts(h["stateHistory"]["stateTimestamp"]) for h in history
              if h.get("stateHistory", {}).get("state") == "TURNED_IN" and h["stateHistory"].get("stateTimestamp")]
    turned_in = max([t for t in turned if t], default=None)

    signals = entrega_signals(sub, work)
    for att in attachments:
        if att.origin == "drive":
            signals += await drive_signals(ds, att, student_email, turned_in)
        elif att.origin == "link" and att.meta.get("github"):
            signals += await github_signals(att, github_token, due, turned_in)
    files = [f for a in attachments for f in a.files]
    signals += content_signals(files)
    return {
        "sinais": signals,
        "atencao": sum(1 for s in signals if s["nivel"] == "atencao"),
        "perguntas": oral_questions(files),
    }


def format_signals(student: str, r: dict) -> str:
    L = ["SINAIS DE AUTORIA: %s" % student]
    atencao = [s for s in r["sinais"] if s["nivel"] == "atencao"]
    info = [s for s in r["sinais"] if s["nivel"] != "atencao"]
    L.append("Pontos de atencao: %d" % len(atencao))
    for s in atencao:
        L.append("  ! " + s["texto"])
    if info:
        L.append("Informativos:")
        for s in info:
            L.append("  - " + s["texto"])
    L.append("Perguntas para uma conversa de verificacao:")
    for q in r["perguntas"]:
        L.append("  ? " + q)
    L += ["", "Estes sinais nao provam autoria nem uso de IA. Use-os para decidir com quem conversar."]
    return "\n".join(L)

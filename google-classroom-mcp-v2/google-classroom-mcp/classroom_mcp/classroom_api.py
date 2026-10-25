"""Acesso ao Google Classroom/Drive: servicos, paginacao, roster e parsers de URL."""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Callable, Optional

from googleapiclient.discovery import build

from .auth_core import load_credentials

_services: Optional[tuple] = None
_roster_cache: dict[str, dict[str, dict]] = {}


def get_services():
    """Retorna (classroom, drive). Nunca abre o browser: orienta a rodar auth.py."""
    global _services
    if _services is None:
        creds = load_credentials(interactive=False)
        _services = (
            build("classroom", "v1", credentials=creds, cache_discovery=False),
            build("drive", "v3", credentials=creds, cache_discovery=False),
        )
    return _services


def reset_services() -> None:
    global _services
    _services = None
    _roster_cache.clear()


async def run(fn: Callable, *args, **kwargs):
    """Executa uma chamada bloqueante fora do loop de eventos.

    Os clientes do Google nao sao thread-safe: chame sempre em sequencia (await),
    nunca em paralelo com gather sobre o mesmo servico.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


def list_all_sync(method: Callable, key: str, **kwargs) -> list[dict]:
    """Percorre todas as paginas de um metodo list() do Google."""
    items: list[dict] = []
    token = None
    while True:
        if token:
            kwargs["pageToken"] = token
        res = method(**kwargs).execute()
        items.extend(res.get(key, []))
        token = res.get("nextPageToken")
        if not token:
            return items


async def list_all(method: Callable, key: str, **kwargs) -> list[dict]:
    return await run(list_all_sync, method, key, **kwargs)


# --------------------------------------------------------------------------
# IDs e URLs
# --------------------------------------------------------------------------

def decode_classroom_id(raw: str) -> str:
    """IDs em links do Classroom costumam vir em base64 do ID numerico da API."""
    if not raw or raw.isdigit():
        return raw
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("ascii")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return raw


_URL_PATTERNS = [
    r"classroom\.google\.com/(?:u/\d+/)?c/([^/?#]+)/[aw]/([^/?#]+)",
    r"classroom\.google\.com/(?:u/\d+/)?g/tg/([^/?#]+)/([^/?#]+)",
    r"classroom\.google\.com/(?:u/\d+/)?c/([^/?#]+)",
]


def parse_classroom_url(url: str) -> dict:
    """Extrai courseId e courseWorkId de um link do Classroom."""
    for p in _URL_PATTERNS:
        m = re.search(p, url or "")
        if m:
            g = m.groups()
            out = {"courseId": decode_classroom_id(g[0])}
            if len(g) > 1:
                out["courseWorkId"] = decode_classroom_id(g[1])
            return out
    return {}


def resolve_ids(args: dict) -> tuple[Optional[str], Optional[str]]:
    course_id = args.get("courseId")
    coursework_id = args.get("courseWorkId")
    url = args.get("url") or ""
    if url:
        parsed = parse_classroom_url(url)
        course_id = course_id or parsed.get("courseId")
        coursework_id = coursework_id or parsed.get("courseWorkId")
    return course_id, coursework_id


# --------------------------------------------------------------------------
# Roster (alunos)
# --------------------------------------------------------------------------

async def get_roster(cs, course_id: str) -> dict[str, dict]:
    """userId -> {name, email}. Uma chamada paginada por turma, com cache."""
    if course_id in _roster_cache:
        return _roster_cache[course_id]
    students = await list_all(cs.courses().students().list, "students", courseId=course_id)
    roster: dict[str, dict] = {}
    for s in students:
        prof = s.get("profile", {})
        roster[s.get("userId", "")] = {
            "name": prof.get("name", {}).get("fullName") or "Aluno %s" % s.get("userId"),
            "email": prof.get("emailAddress", ""),
        }
    # Nomes repetidos recebem sufixo para nao colidirem em relatorios
    seen: dict[str, int] = {}
    for uid, info in roster.items():
        seen[info["name"]] = seen.get(info["name"], 0) + 1
    for uid, info in roster.items():
        if seen[info["name"]] > 1:
            info["name"] = "%s (%s)" % (info["name"], info["email"] or uid[-4:])
    _roster_cache[course_id] = roster
    return roster


async def student_info(cs, course_id: str, user_id: str) -> dict:
    roster = await get_roster(cs, course_id)
    if user_id in roster:
        return roster[user_id]
    try:
        p = await run(cs.userProfiles().get(userId=user_id).execute)
        return {"name": p.get("name", {}).get("fullName", "Aluno %s" % user_id),
                "email": p.get("emailAddress", "")}
    except Exception:
        return {"name": "Aluno %s" % user_id, "email": ""}


# --------------------------------------------------------------------------
# Entregas
# --------------------------------------------------------------------------

STATE_ICON = {
    "TURNED_IN": "[entregue]", "RETURNED": "[devolvida]", "NEW": "[nao entregue]",
    "CREATED": "[rascunho do aluno]", "RECLAIMED_BY_STUDENT": "[retomada pelo aluno]",
}


def fmt_due(work: dict) -> str:
    d = work.get("dueDate")
    if not d:
        return "sem prazo"
    s = "%02d/%02d/%04d" % (d.get("day", 0), d.get("month", 0), d.get("year", 0))
    t = work.get("dueTime")
    if t:
        s += " %02d:%02d UTC" % (t.get("hours", 0), t.get("minutes", 0))
    return s


def attachments_of(sub: dict) -> list[dict]:
    return sub.get("assignmentSubmission", {}).get("attachments", []) or []


def has_grade(sub: dict) -> bool:
    return sub.get("assignedGrade") is not None or sub.get("draftGrade") is not None


def grade_text(sub: dict) -> str:
    if sub.get("assignedGrade") is not None:
        return "%s (definitiva)" % sub["assignedGrade"]
    if sub.get("draftGrade") is not None:
        return "%s (rascunho)" % sub["draftGrade"]
    return "sem nota"


async def list_submissions(cs, course_id: str, coursework_id: str) -> list[dict]:
    return await list_all(
        cs.courses().courseWork().studentSubmissions().list, "studentSubmissions",
        courseId=course_id, courseWorkId=coursework_id,
    )


async def get_submission(cs, course_id: str, coursework_id: str, sub_id: str) -> dict:
    req = cs.courses().courseWork().studentSubmissions().get(
        courseId=course_id, courseWorkId=coursework_id, id=sub_id
    )
    return await run(req.execute)


async def get_coursework(cs, course_id: str, coursework_id: str) -> dict:
    req = cs.courses().courseWork().get(courseId=course_id, id=coursework_id)
    return await run(req.execute)


def http_status(exc: Exception) -> Optional[int]:
    resp = getattr(exc, "resp", None)
    return getattr(resp, "status", None)


def explain_http_error(exc: Exception) -> str:
    status = http_status(exc)
    if status == 403:
        return "sem permissao (403). Se for uma nota, a atividade pode ter sido criada fora deste projeto do Google Cloud."
    if status == 404:
        return "nao encontrado (404). Confira o ID ou se voce tem acesso."
    if status == 429:
        return "limite de requisicoes do Google atingido (429). Tente de novo em instantes."
    return str(exc)

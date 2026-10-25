"""Armazenamento local (SQLite): rubricas, rascunhos de feedback e historico de plagio.

Fica em ~/.classroom-mcp/classroom.db, so no seu computador.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import closing
from datetime import datetime, timezone

from .config import BASE_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS rubricas (
  course_id TEXT, coursework_id TEXT, json TEXT, updated TEXT,
  PRIMARY KEY (course_id, coursework_id));
CREATE TABLE IF NOT EXISTS rascunhos (
  course_id TEXT, coursework_id TEXT, submission_id TEXT, user_id TEXT, nome TEXT,
  nota REAL, feedback TEXT, criterios TEXT, status TEXT, canal TEXT, publicado_em TEXT, updated TEXT,
  PRIMARY KEY (course_id, coursework_id, submission_id));
CREATE TABLE IF NOT EXISTS fingerprints (
  id INTEGER PRIMARY KEY AUTOINCREMENT, course_id TEXT, coursework_id TEXT, user_id TEXT,
  name TEXT, label TEXT, kind TEXT, lit TEXT, est TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS ix_fp_work ON fingerprints (course_id, coursework_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    new = not DB_PATH.exists()
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    if new:
        try:
            DB_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    return c


# ----------------------------- rubricas -----------------------------------

def validate_rubrica(criterios: list[dict], max_points: float | None) -> tuple[list[dict], list[str]]:
    """Normaliza criterios e devolve (criterios, avisos). Lanca ValueError se invalida."""
    if not criterios:
        raise ValueError("Informe ao menos um criterio.")
    out, avisos = [], []
    for i, c in enumerate(criterios, 1):
        nome = str(c.get("nome", "")).strip()
        try:
            pontos = float(c.get("pontos"))
        except (TypeError, ValueError):
            raise ValueError("Criterio %d (%s): 'pontos' deve ser numero." % (i, nome or "sem nome"))
        if not nome or pontos <= 0:
            raise ValueError("Criterio %d: informe 'nome' e 'pontos' maior que zero." % i)
        out.append({"nome": nome, "pontos": pontos, "descricao": str(c.get("descricao", "")).strip()})
    total = sum(c["pontos"] for c in out)
    if max_points is not None and abs(total - float(max_points)) > 1e-6:
        avisos.append("A soma dos criterios (%g) difere da pontuacao da atividade (%g)." % (total, float(max_points)))
    return out, avisos


def save_rubrica(course_id: str, coursework_id: str, rubrica: dict) -> None:
    with closing(connect()) as c, c:
        c.execute("INSERT OR REPLACE INTO rubricas VALUES (?,?,?,?)",
                  (course_id, coursework_id, json.dumps(rubrica, ensure_ascii=False), now()))


def get_rubrica(course_id: str, coursework_id: str) -> dict | None:
    with closing(connect()) as c:
        r = c.execute("SELECT json FROM rubricas WHERE course_id=? AND coursework_id=?",
                      (course_id, coursework_id)).fetchone()
    return json.loads(r["json"]) if r else None


# ----------------------------- rascunhos ----------------------------------

def upsert_rascunho(course_id: str, coursework_id: str, submission_id: str, user_id: str, nome: str,
                    nota: float | None, feedback: str, criterios: list | None) -> None:
    with closing(connect()) as c, c:
        c.execute("""INSERT INTO rascunhos (course_id, coursework_id, submission_id, user_id, nome, nota, feedback,
                     criterios, status, canal, publicado_em, updated) VALUES (?,?,?,?,?,?,?,?, 'rascunho', NULL, NULL, ?)
                     ON CONFLICT(course_id, coursework_id, submission_id) DO UPDATE SET
                       user_id=excluded.user_id, nome=excluded.nome, nota=excluded.nota, feedback=excluded.feedback,
                       criterios=excluded.criterios, status='rascunho', updated=excluded.updated""",
                  (course_id, coursework_id, submission_id, user_id, nome, nota, feedback,
                   json.dumps(criterios or [], ensure_ascii=False), now()))


def list_rascunhos(course_id: str, coursework_id: str, status: str | None = None) -> list[dict]:
    q = "SELECT * FROM rascunhos WHERE course_id=? AND coursework_id=?"
    args: list = [course_id, coursework_id]
    if status:
        q += " AND status=?"
        args.append(status)
    with closing(connect()) as c:
        rows = c.execute(q + " ORDER BY nome", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["criterios"] = json.loads(d["criterios"] or "[]")
        out.append(d)
    return out


def mark_rascunho(course_id: str, coursework_id: str, submission_id: str, status: str, canal: str = "") -> None:
    with closing(connect()) as c, c:
        c.execute("UPDATE rascunhos SET status=?, canal=?, publicado_em=?, updated=? "
                  "WHERE course_id=? AND coursework_id=? AND submission_id=?",
                  (status, canal, now() if status == "publicado" else None, now(), course_id, coursework_id, submission_id))


# ----------------------------- plagio -------------------------------------

def save_fingerprints(course_id: str, coursework_id: str, label: str, fingerprints: dict[str, dict],
                      user_ids: dict[str, str]) -> int:
    """fingerprints: nome -> {kind: {lit, est}}; user_ids: nome -> userId."""
    n = 0
    with closing(connect()) as c, c:
        c.execute("DELETE FROM fingerprints WHERE course_id=? AND coursework_id=?", (course_id, coursework_id))
        for name, kinds in fingerprints.items():
            for kind, fp in kinds.items():
                c.execute("INSERT INTO fingerprints (course_id, coursework_id, user_id, name, label, kind, lit, est, created) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (course_id, coursework_id, user_ids.get(name, ""), name, label, kind,
                           json.dumps(fp["lit"]), json.dumps(fp["est"]), now()))
                n += 1
    return n


def load_history(exclude_course: str, exclude_work: str) -> list[dict]:
    with closing(connect()) as c:
        rows = c.execute("SELECT name, label, kind, lit, est FROM fingerprints "
                         "WHERE NOT (course_id=? AND coursework_id=?)", (exclude_course, exclude_work)).fetchall()
    return [{"name": r["name"], "label": r["label"], "kind": r["kind"],
             "lit": json.loads(r["lit"]), "est": json.loads(r["est"])} for r in rows]

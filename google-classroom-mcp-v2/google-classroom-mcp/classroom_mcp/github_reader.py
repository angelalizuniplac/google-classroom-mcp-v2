"""Leitura de repositorios GitHub (publicos, ou privados com token)."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import httpx

from . import cache
from .config import GITHUB_TOKEN_ENV, MAX_GITHUB_FILE_BYTES, MAX_GITHUB_TOTAL_BYTES
from .filetypes import is_readable_name, read_bytes
from .models import FileItem

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

_GH_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s?#]+?)(?:\.git)?(?:/(tree|blob)/(.+?))?/?$",
    re.IGNORECASE,
)


def parse_github_url(url: str) -> dict:
    """Normaliza URLs do GitHub (barra final, query, .git, /tree/, /blob/).

    Retorna {owner, repo, mode, rest}; mode em {"repo", "tree", "blob"}.
    Lanca ValueError se nao for um link de repositorio.
    """
    clean = re.split(r"[?#]", (url or "").strip(), maxsplit=1)[0].rstrip("/")
    m = _GH_RE.match(clean)
    if not m:
        raise ValueError("URL do GitHub invalida: %s" % url)
    owner, repo, mode, rest = m.groups()
    return {"owner": owner, "repo": repo, "mode": mode or "repo", "rest": rest or ""}


def split_ref_candidates(rest: str) -> list[tuple[str, str]]:
    """Branches podem ter barra (feature/x). Gera (ref, caminho) do mais longo ao mais curto."""
    parts = [p for p in rest.split("/") if p]
    return [("/".join(parts[:i]), "/".join(parts[i:])) for i in range(len(parts), 0, -1)]


def get_token(explicit: Optional[str] = None) -> Optional[str]:
    return explicit or os.environ.get(GITHUB_TOKEN_ENV) or None


def _headers(token: Optional[str], raw: bool = False) -> dict:
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
         "User-Agent": "google-classroom-mcp"}
    if token:
        h["Authorization"] = "Bearer %s" % token
    return h


def _api_error(resp: httpx.Response, owner: str, repo: str) -> str:
    if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
        return ("Limite de requisicoes do GitHub atingido. Defina a variavel de ambiente %s "
                "(token com permissao Contents: Read) e reinicie o Claude Desktop." % GITHUB_TOKEN_ENV)
    if resp.status_code in (401, 403):
        return "Sem permissao para %s/%s. O token informado nao tem acesso a este repositorio." % (owner, repo)
    if resp.status_code == 404:
        return ("Repositorio %s/%s nao encontrado. Ele pode ser privado (informe um token) ou o link esta errado."
                % (owner, repo))
    return "GitHub respondeu HTTP %d para %s/%s." % (resp.status_code, owner, repo)


@dataclass
class RepoResult:
    owner: str = ""
    repo: str = ""
    ref: str = ""
    items: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    error: str = ""


async def read_repo(url: str, token: Optional[str] = None, paths: Optional[list[str]] = None) -> RepoResult:
    """Le os arquivos legiveis de um repositorio. `paths` filtra por prefixo/nome."""
    try:
        info = parse_github_url(url)
    except ValueError as e:
        return RepoResult(error=str(e))
    owner, repo = info["owner"], info["repo"]
    token = get_token(token)
    res = RepoResult(owner=owner, repo=repo)

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get("%s/repos/%s/%s" % (API, owner, repo), headers=_headers(token))
        if r.status_code != 200:
            res.error = _api_error(r, owner, repo)
            return res
        repo_meta = r.json()
        default_branch = repo_meta.get("default_branch", "main")
        pushed_at = repo_meta.get("pushed_at", "")

        # Descobrir a ref (branch) e o subcaminho
        subpath = ""
        tree = None
        if info["mode"] in ("tree", "blob"):
            for ref, sub in split_ref_candidates(info["rest"]):
                t = await client.get("%s/repos/%s/%s/git/trees/%s?recursive=1" % (API, owner, repo, quote(ref, safe="")),
                                     headers=_headers(token))
                if t.status_code == 200:
                    res.ref, subpath, tree = ref, sub, t.json()
                    break
        if tree is None:
            res.ref = default_branch
            t = await client.get("%s/repos/%s/%s/git/trees/%s?recursive=1" % (API, owner, repo, quote(res.ref, safe="")),
                                 headers=_headers(token))
            if t.status_code != 200:
                res.error = ("Repositorio vazio ou branch '%s' nao encontrada." % res.ref
                             if t.status_code in (404, 409) else _api_error(t, owner, repo))
                return res
            tree = t.json()

        blobs = [i for i in tree.get("tree", []) if i.get("type") == "blob"]
        res.meta = {"branch": res.ref, "branch_padrao": default_branch, "pushed_at": pushed_at,
                    "arquivos_no_repo": len(blobs), "truncado_pela_api": bool(tree.get("truncated")),
                    "ignorados": 0, "omitidos_por_tamanho": [], "omitidos_por_limite": 0}

        wanted = []
        for b in blobs:
            path = b["path"]
            if subpath and not (path == subpath or path.startswith(subpath.rstrip("/") + "/")):
                continue
            if paths and not any(path == p or path.startswith(p.rstrip("/") + "/") or p.lower() in path.lower() for p in paths):
                continue
            if not is_readable_name(path):
                res.meta["ignorados"] += 1
                continue
            if b.get("size", 0) > MAX_GITHUB_FILE_BYTES:
                res.meta["omitidos_por_tamanho"].append("%s (%d bytes)" % (path, b.get("size", 0)))
                continue
            wanted.append(b)

        # Cache por versao do repositorio (pushed_at)
        key = "gh|%s|%s|%s|%s|%s" % (owner, repo, res.ref, pushed_at, "|".join(sorted(paths or [])) + "|" + subpath)
        cached = cache.get_items(key)
        if cached is not None:
            res.items = cached
            res.meta["cache"] = True
            return res

        total = 0
        selected = []
        for b in wanted:
            if total + b.get("size", 0) > MAX_GITHUB_TOTAL_BYTES:
                res.meta["omitidos_por_limite"] += 1
                continue
            total += b.get("size", 0)
            selected.append(b)

        sem = asyncio.Semaphore(6)

        async def fetch(b: dict) -> list[FileItem]:
            path = b["path"]
            async with sem:
                if token:  # API de conteudo funciona igual para repos privados
                    resp = await client.get("%s/repos/%s/%s/contents/%s?ref=%s" % (API, owner, repo, quote(path), quote(res.ref, safe="")),
                                            headers=_headers(token, raw=True))
                else:      # repositorio publico: raw nao consome o limite da API
                    resp = await client.get("%s/%s/%s/%s/%s" % (RAW, owner, repo, quote(res.ref, safe=""), quote(path)),
                                            headers=_headers(None))
            if resp.status_code != 200:
                return [FileItem(name=path, kind="other", error="HTTP %d ao baixar" % resp.status_code, source="github")]
            return read_bytes(path, resp.content, source="github")

        results = await asyncio.gather(*(fetch(b) for b in selected))
        res.items = [it for group in results for it in group]
        cache.put_items(key, res.items)
    return res


async def get_commits(owner: str, repo: str, ref: str, token: Optional[str] = None, max_pages: int = 3) -> list[dict]:
    """Ate max_pages*100 commits mais recentes da branch."""
    token = get_token(token)
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            r = await client.get("%s/repos/%s/%s/commits" % (API, owner, repo),
                                 params={"sha": ref, "per_page": 100, "page": page}, headers=_headers(token))
            if r.status_code != 200:
                break
            batch = r.json()
            for c in batch:
                commit = c.get("commit", {})
                out.append({
                    "sha": c.get("sha", "")[:7],
                    "date": commit.get("author", {}).get("date", ""),
                    "autor": commit.get("author", {}).get("name", ""),
                    "mensagem": (commit.get("message", "") or "").splitlines()[0][:100] if commit.get("message") else "",
                })
            if len(batch) < 100:
                break
    return out

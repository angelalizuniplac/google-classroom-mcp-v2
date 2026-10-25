"""Leitura de links (GitHub, Drive, Colab, Docs, paginas e arquivos soltos) e
carregamento completo dos anexos de uma entrega."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from . import drive_reader, github_reader
from .classroom_api import attachments_of
from .filetypes import read_bytes
from .models import AttachmentResult, FileItem
from .safety import UnsafeUrl, safe_get, scan_text

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

_DRIVE_FILE = [
    r"drive\.google\.com/file/d/([\w-]+)",
    r"drive\.google\.com/(?:open|uc)\?(?:[^#\s]*&)?id=([\w-]+)",
    r"docs\.google\.com/(?:document|spreadsheets|presentation)/d/([\w-]+)",
    r"colab\.research\.google\.com/drive/([\w-]+)",
]
_DRIVE_FOLDER = r"drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]+)"
_COLAB_GITHUB = r"colab\.research\.google\.com/github/([^/]+)/([^/]+)/blob/([^/]+)/([^?#]+)"
_GIST = r"gist\.github\.com/(?:[^/]+/)?([0-9a-f]{8,})"


def extract_urls(text: str) -> list[str]:
    seen, out = set(), []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def classify_url(url: str) -> tuple[str, tuple]:
    """Retorna (tipo, grupos). Tipos: github, gist, colab_github, drive_file, drive_folder, forms, web."""
    for p in _DRIVE_FILE:
        m = re.search(p, url)
        if m:
            return "drive_file", m.groups()
    m = re.search(_DRIVE_FOLDER, url)
    if m:
        return "drive_folder", m.groups()
    if re.search(r"docs\.google\.com/forms/", url):
        return "forms", ()
    m = re.search(_COLAB_GITHUB, url)
    if m:
        return "colab_github", m.groups()
    m = re.search(_GIST, url)
    if m and "gist.github.com" in url:
        return "gist", m.groups()
    if re.match(r"^https?://(?:www\.)?github\.com/[^/]+/[^/]+", url):
        return "github", ()
    return "web", ()


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return "\n".join(p.parts)


def _name_from_url(url: str, default: str = "pagina.html") -> str:
    path = unquote(urlparse(url).path)
    base = PurePosixPath(path).name
    return base or default


async def read_url(url: str, ds, token: str | None = None) -> tuple[list[FileItem], dict]:
    """Le um link. Retorna (arquivos, meta). Nunca lanca."""
    kind, g = classify_url(url)
    meta: dict = {"tipo_link": kind}
    try:
        if kind == "github":
            res = await github_reader.read_repo(url, token)
            meta["github"] = {"owner": res.owner, "repo": res.repo, "ref": res.ref, **res.meta}
            if res.error:
                return [FileItem(name=url, kind="other", source="github", error=res.error)], meta
            return res.items, meta

        if kind == "drive_file":
            return await drive_reader.read_drive_file(ds, g[0], "arquivo do Drive"), meta
        if kind == "drive_folder":
            return await drive_reader.read_drive_folder(ds, g[0]), meta
        if kind == "forms":
            return [FileItem(name=url, kind="other", warnings=["link de Google Forms: nao e possivel ler as respostas"])], meta

        if kind == "colab_github":
            owner, repo, branch, path = g
            raw = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (owner, repo, branch, path)
            data, ctype, _ = await safe_get(raw)
            return read_bytes(PurePosixPath(path).name, data, ctype, source="link"), meta

        if kind == "gist":
            data, _, _ = await safe_get("https://api.github.com/gists/%s" % g[0],
                                        headers={"User-Agent": "google-classroom-mcp", "Accept": "application/vnd.github+json"})
            gist = json.loads(data)
            items: list[FileItem] = []
            for fname, f in (gist.get("files") or {}).items():
                content = (f.get("content") or "").encode("utf-8")
                items += read_bytes(fname, content, f.get("type", ""), source="link")
            return items or [FileItem(name=url, kind="other", warnings=["gist vazio"])], meta

        # Pagina ou arquivo generico
        data, ctype, final = await safe_get(url)
        name = _name_from_url(final)
        if "html" in ctype.lower():
            text = html_to_text(data.decode("utf-8", errors="replace"))
            item = FileItem(name=name or "pagina.html", kind="text", text=text[:60000], source="link", size=len(data))
            item.warnings = scan_text(text)
            if len(text) > 60000:
                item.warnings.append("pagina cortada em 60.000 caracteres")
            if not text.strip():
                item.warnings.append("pagina sem texto (pode depender de JavaScript ou exigir login)")
            return [item], meta
        return read_bytes(name, data, ctype.split(";")[0], source="link"), meta

    except UnsafeUrl as e:
        return [FileItem(name=url, kind="other", source="link", error="link nao acessado: %s" % e)], meta
    except Exception as e:
        return [FileItem(name=url, kind="other", source="link", error="erro ao ler o link: %s" % e)], meta


async def load_attachments(ds, sub: dict, github_token: str | None = None) -> list[AttachmentResult]:
    """Le todos os anexos (e links dentro de resposta curta) de uma entrega."""
    results: list[AttachmentResult] = []
    seen_urls: set[str] = set()

    def next_idx() -> int:
        return len(results) + 1

    short = (sub.get("shortAnswerSubmission") or {}).get("answer", "")
    mc = (sub.get("multipleChoiceSubmission") or {}).get("answer", "")
    texto = short or mc
    pending_urls = extract_urls(short)
    if texto:
        item = FileItem(name="resposta_curta.txt", kind="text", text=texto, plag_text=short, plag_kind="prose" if short else "",
                        source="classroom", size=len(texto), warnings=scan_text(texto))
        results.append(AttachmentResult(idx=next_idx(), title="Resposta no Classroom", origin="texto", files=[item]))

    for att in attachments_of(sub):
        if "driveFile" in att:
            df = att["driveFile"]
            title = df.get("title", "arquivo")
            files = await drive_reader.read_drive_file(ds, df.get("id", ""), title)
            results.append(AttachmentResult(idx=next_idx(), title=title, origin="drive",
                                            url=df.get("alternateLink", ""), files=files,
                                            meta={"drive_id": df.get("id", "")}))
        elif "link" in att:
            url = att["link"].get("url", "")
            seen_urls.add(url)
            files, meta = await read_url(url, ds, github_token)
            results.append(AttachmentResult(idx=next_idx(), title=att["link"].get("title") or url, origin="link",
                                            url=url, files=files, meta=meta))
        elif "youTubeVideo" in att:
            yt = att["youTubeVideo"]
            results.append(AttachmentResult(idx=next_idx(), title=yt.get("title", "video"), origin="youtube",
                                            url=yt.get("alternateLink", ""),
                                            note="video do YouTube: nao e possivel avaliar o conteudo"))
        elif "form" in att:
            f = att["form"]
            results.append(AttachmentResult(idx=next_idx(), title=f.get("title", "formulario"), origin="form",
                                            url=f.get("formUrl", ""), note="Google Forms: nao e possivel ler as respostas"))

    for url in pending_urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        files, meta = await read_url(url, ds, github_token)
        results.append(AttachmentResult(idx=next_idx(), title="link na resposta: " + url, origin="link",
                                        url=url, files=files, meta=meta))
    return results

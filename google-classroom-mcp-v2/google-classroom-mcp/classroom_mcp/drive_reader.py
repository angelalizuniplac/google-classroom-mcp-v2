"""Leitura de arquivos e pastas do Google Drive (anexos de entregas, Colab, Docs)."""

from __future__ import annotations

import io

from googleapiclient.http import MediaIoBaseDownload

from . import cache
from .classroom_api import http_status, list_all_sync, run
from .config import MAX_DRIVE_BYTES, MAX_FOLDER_FILES
from .filetypes import read_bytes
from .models import FileItem

GOOGLE_EXPORT = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
META_FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,webViewLink,owners(displayName,emailAddress),lastModifyingUser(displayName,emailAddress),shortcutDetails"


def drive_error_message(exc: Exception) -> str:
    status = http_status(exc)
    if status in (401, 403):
        return ("sem acesso ao arquivo (compartilhamento restrito). Peca ao aluno para compartilhar "
                "com a sua conta ou para trocar o acesso para 'qualquer pessoa com o link'.")
    if status == 404:
        return "arquivo nao encontrado (apagado ou sem acesso)."
    return "erro ao acessar o Drive: %s" % exc


def _download(ds, file_id: str, export_mime: str | None = None) -> bytes:
    if export_mime:
        req = ds.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        req = ds.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


async def get_drive_meta(ds, file_id: str) -> dict:
    return await run(ds.files().get(fileId=file_id, fields=META_FIELDS, supportsAllDrives=True).execute)


async def read_drive_file(ds, file_id: str, fallback_name: str = "arquivo", depth: int = 0) -> list[FileItem]:
    """Le um arquivo do Drive. Nunca lanca: erros viram FileItem.error."""
    try:
        meta = await get_drive_meta(ds, file_id)
    except Exception as e:
        return [FileItem(name=fallback_name, kind="other", source="drive", error=drive_error_message(e))]

    name = meta.get("name") or fallback_name
    mime = meta.get("mimeType", "")
    size = int(meta.get("size", 0) or 0)

    if mime == SHORTCUT_MIME and meta.get("shortcutDetails", {}).get("targetId") and depth < 2:
        return await read_drive_file(ds, meta["shortcutDetails"]["targetId"], name, depth + 1)
    if mime == FOLDER_MIME:
        return await read_drive_folder(ds, file_id, name, depth)
    if size > MAX_DRIVE_BYTES:
        return [FileItem(name=name, kind="other", size=size, source="drive",
                         error="arquivo muito grande (%d MB), nao lido" % (size // 1_000_000))]

    cache_key = "drive|%s|%s" % (file_id, meta.get("modifiedTime", ""))
    cached = cache.get_items(cache_key)
    if cached is not None and not any(i.kind == "image" for i in cached):
        return cached

    try:
        warnings: list[str] = []
        if mime in GOOGLE_EXPORT:
            export_mime, ext = GOOGLE_EXPORT[mime]
            data = await run(_download, ds, file_id, export_mime)
            read_name = name if name.lower().endswith(ext) else name + ext
            if mime.endswith("spreadsheet"):
                warnings.append("planilha do Google exportada como CSV: apenas a primeira aba foi lida")
            items = read_bytes(read_name, data, export_mime, source="drive")
        elif mime.startswith("application/vnd.google-apps."):
            return [FileItem(name=name, kind="other", source="drive",
                             warnings=["tipo do Google nao suportado: %s" % mime])]
        else:
            data = await run(_download, ds, file_id)
            items = read_bytes(name, data, mime, source="drive")
        for it in items:
            it.meta["drive"] = {"id": file_id, "modifiedTime": meta.get("modifiedTime"),
                                "createdTime": meta.get("createdTime"), "link": meta.get("webViewLink")}
            it.warnings = warnings + it.warnings if it is items[0] else it.warnings
        cache.put_items(cache_key, items)
        return items
    except Exception as e:
        return [FileItem(name=name, kind="other", source="drive", error=drive_error_message(e))]


async def read_drive_folder(ds, folder_id: str, name: str = "pasta", depth: int = 0) -> list[FileItem]:
    try:
        files = await run(
            list_all_sync, ds.files().list, "files",
            q="'%s' in parents and trashed=false" % folder_id,
            fields="nextPageToken,files(id,name,mimeType,size)", pageSize=100,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        )
    except Exception as e:
        return [FileItem(name=name, kind="other", source="drive", error=drive_error_message(e))]
    items: list[FileItem] = []
    for f in files[:MAX_FOLDER_FILES]:
        if f.get("mimeType") == FOLDER_MIME:
            if depth < 1:
                items += await read_drive_folder(ds, f["id"], "%s/%s" % (name, f["name"]), depth + 1)
            continue
        for it in await read_drive_file(ds, f["id"], f.get("name", "arquivo"), depth + 1):
            it.name = "%s/%s" % (name, it.name) if not it.name.startswith(name) else it.name
            items.append(it)
    if len(files) > MAX_FOLDER_FILES:
        items.append(FileItem(name=name, kind="other", source="drive",
                              warnings=["pasta com %d arquivos; lidos apenas os primeiros %d" % (len(files), MAX_FOLDER_FILES)]))
    if not files:
        items.append(FileItem(name=name, kind="other", source="drive", warnings=["pasta vazia"]))
    return items

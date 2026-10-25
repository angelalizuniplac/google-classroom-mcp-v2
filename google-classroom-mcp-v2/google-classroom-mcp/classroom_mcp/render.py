"""Transforma entregas lidas em respostas compactas e com orcamento de tokens."""

from __future__ import annotations

from mcp.types import ImageContent, TextContent

from .config import (DEFAULT_CHARS_PER_FILE, DEFAULT_OUTPUT_BUDGET, MAX_IMAGES_PER_CALL)
from .models import AttachmentResult, FileItem
from .safety import UNTRUSTED_NOTICE, wrap_student_content
from .textutil import clip, clip_notice, compact_text, est_tokens

MAX_FILES_LISTED = 60


def file_tokens(f: FileItem) -> int:
    return est_tokens(len(compact_text(f.text))) if f.text else 0


def _flags(f: FileItem) -> str:
    bits = []
    if f.error:
        bits.append("ERRO")
    if f.warnings:
        bits.append("%d aviso(s)" % len(f.warnings))
    if f.images:
        bits.append("%d imagem(ns)" % len(f.images))
    return ", ".join(bits)


def summarize(results: list[AttachmentResult]) -> str:
    """Indice da entrega: sem conteudo dos arquivos, so metadados e avisos (barato)."""
    L: list[str] = []
    total_tokens = 0
    for a in results:
        L.append("[anexo %d] %s (%s)%s" % (a.idx, a.title, a.origin, (" " + a.url) if a.url else ""))
        if a.note:
            L.append("   nota: " + a.note)
        gh = a.meta.get("github")
        if gh and not a.error:
            L.append("   GitHub %s/%s branch %s: %s arquivo(s) no repo, %s ignorado(s) por tipo, %s omitido(s) por tamanho, ultimo push %s%s" % (
                gh.get("owner"), gh.get("repo"), gh.get("ref"), gh.get("arquivos_no_repo", "?"), gh.get("ignorados", 0),
                len(gh.get("omitidos_por_tamanho", [])), gh.get("pushed_at", "?"), " (do cache)" if gh.get("cache") else ""))
            for o in gh.get("omitidos_por_tamanho", [])[:5]:
                L.append("   omitido por tamanho: " + o)
        listed = a.files[:MAX_FILES_LISTED]
        for f in listed:
            t = file_tokens(f)
            total_tokens += t
            extra = ""
            if f.kind == "notebook":
                s = f.meta.get("stats", {})
                extra = " | %s cel. codigo, %s markdown, %s nao executadas, %s com erro" % (
                    s.get("codigo"), s.get("markdown"), s.get("nao_executadas"), len(s.get("com_erro", [])))
            elif f.kind == "table":
                extra = " | %s x %s" % (f.meta.get("linhas"), f.meta.get("colunas"))
            elif f.kind == "pdf":
                extra = " | %s pag." % f.meta.get("paginas", "?")
            L.append("   - %s [%s] %d linhas, ~%d tokens%s%s" % (
                f.name, f.kind, f.n_lines, t, extra, (" | " + _flags(f)) if _flags(f) else ""))
            if f.error:
                L.append("       erro: " + f.error)
            for w in f.warnings[:3]:
                L.append("       aviso: " + w)
        if len(a.files) > MAX_FILES_LISTED:
            rest = a.files[MAX_FILES_LISTED:]
            total_tokens += sum(file_tokens(f) for f in rest)
            L.append("   ... mais %d arquivo(s) (use ler_arquivo com arquivo=<trecho do nome>)" % len(rest))
    L.append("")
    L.append("Total aproximado se tudo fosse lido: ~%d tokens. Use ler_arquivo (anexo, arquivo, offset, limite) "
             "para ler so o que importa, ou ler_entrega_aluno com orcamento." % total_tokens)
    return "\n".join(L)


def render_entrega(header: str, results: list[AttachmentResult], budget: int = DEFAULT_OUTPUT_BUDGET,
                   per_file: int = DEFAULT_CHARS_PER_FILE, include_images: bool = True) -> list:
    """Conteudo completo, dentro do orcamento. Prioriza arquivos pequenos e de codigo/texto."""
    out: list = [TextContent(type="text", text=header + "\n" + UNTRUSTED_NOTICE)]
    used = 0
    omitted: list[str] = []
    images_left = MAX_IMAGES_PER_CALL if include_images else 0
    all_files = [(a, f) for a in results for f in a.files]

    for a in results:
        if a.note or a.error:
            out.append(TextContent(type="text", text="[anexo %d] %s: %s" % (a.idx, a.title, a.note or a.error)))

    # Ordem de leitura: mantem a ordem original, mas pula (para o fim) arquivos enormes
    for a, f in all_files:
        label = "[anexo %d] %s" % (a.idx, f.name)
        if f.error:
            out.append(TextContent(type="text", text="%s: ERRO: %s" % (label, f.error)))
            continue
        body = compact_text(f.text) if f.text else ""
        if f.warnings:
            out.append(TextContent(type="text", text="%s avisos: %s" % (label, " | ".join(f.warnings[:4]))))
        if f.images and images_left > 0:
            for b64, mime in f.images[:images_left]:
                out.append(ImageContent(type="image", data=b64, mimeType=mime))
                images_left -= 1
            out.append(TextContent(type="text", text="%s: imagem acima" % label))
        elif f.images:
            omitted.append("%s (imagem)" % f.name)
        elif f.meta.get("tinha_imagens"):
            omitted.append("%s (imagens nao guardadas no cache: rode limpar_cache e leia de novo)" % f.name)
        if not body:
            continue
        remaining = budget - used
        if remaining < 400:
            omitted.append("%s (~%d tokens)" % (f.name, est_tokens(len(body))))
            continue
        chunk, info = clip(body, min(per_file, remaining))
        used += len(chunk)
        text = "%s (%s, %d linhas)\n%s%s" % (label, f.kind, f.n_lines, wrap_student_content(f.name, chunk), clip_notice(info))
        out.append(TextContent(type="text", text=text))

    footer = "Conteudo enviado: ~%d tokens." % est_tokens(used)
    if omitted:
        footer += "\nNao incluidos por limite de orcamento (leia com ler_arquivo): " + "; ".join(omitted[:25])
    out.append(TextContent(type="text", text=footer))
    return out


def find_files(results: list[AttachmentResult], anexo: int | None, arquivo: str) -> list[tuple[AttachmentResult, FileItem]]:
    needle = (arquivo or "").lower().strip()
    found = []
    for a in results:
        if anexo and a.idx != anexo:
            continue
        for f in a.files:
            if not needle or needle in f.name.lower():
                found.append((a, f))
    return found

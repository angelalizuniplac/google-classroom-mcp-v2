"""Utilitarios para economizar tokens: compactar texto, cortar com aviso, reduzir imagens."""

from __future__ import annotations

import base64
import io
import re

CHARS_PER_TOKEN = 3.6  # estimativa media para texto em portugues e codigo


def est_tokens(chars: int) -> int:
    return int(chars / CHARS_PER_TOKEN)


def compact_text(text: str) -> str:
    """Remove espacos no fim das linhas e colapsa linhas em branco repetidas.

    Nao altera o conteudo util; so reduz o que consome tokens sem informacao.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def clip(text: str, limit: int, offset: int = 0) -> tuple[str, dict]:
    """Devolve o trecho [offset, offset+limit) e informacoes para continuar a leitura."""
    total = len(text)
    chunk = text[offset: offset + limit]
    end = offset + len(chunk)
    info = {"total": total, "inicio": offset, "fim": end, "truncado": end < total}
    return chunk, info


def clip_notice(info: dict) -> str:
    if not info["truncado"]:
        return ""
    return ("\n[... cortado: mostrando caracteres %d a %d de %d (~%d tokens restantes). "
            "Use ler_arquivo com offset=%d para continuar]" % (
                info["inicio"], info["fim"], info["total"], est_tokens(info["total"] - info["fim"]), info["fim"]))


def shrink_image(data: bytes, mime: str = "image/png", max_side: int = 1024) -> tuple[str, str] | None:
    """Reduz imagem para no maximo max_side px e recodifica (JPEG sem transparencia).

    Imagens grandes custam muitos tokens; 1024 px preserva a legibilidade de codigo e graficos.
    Sem Pillow, devolve a imagem original apenas se for pequena.
    """
    try:
        from PIL import Image
    except Exception:
        return (base64.standard_b64encode(data).decode(), mime) if len(data) <= 300_000 else None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        buf = io.BytesIO()
        if has_alpha:
            img.save(buf, format="PNG", optimize=True)
            out_mime = "image/png"
        else:
            img.convert("RGB").save(buf, format="JPEG", quality=72, optimize=True)
            out_mime = "image/jpeg"
        return base64.standard_b64encode(buf.getvalue()).decode(), out_mime
    except Exception:
        return None

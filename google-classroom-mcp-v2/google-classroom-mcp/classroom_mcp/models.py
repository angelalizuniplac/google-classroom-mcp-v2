"""Estruturas de dados usadas pelos leitores e analises."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class FileItem:
    """Um arquivo lido de uma entrega (Drive, GitHub, zip, link)."""

    name: str
    kind: str = "text"        # code | text | notebook | table | pdf | docx | image | other
    text: str = ""            # texto legivel que pode ir para a conversa
    plag_text: str = ""       # texto usado na comparacao de plagio
    plag_kind: str = ""       # "code" | "prose" | "" (nao comparar)
    images: list = field(default_factory=list)   # [(base64, mime)]
    size: int = 0
    source: str = ""          # drive | github | zip | link
    warnings: list = field(default_factory=list)
    error: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def n_lines(self) -> int:
        return self.text.count("\n") + 1 if self.text else 0

    def to_json(self) -> dict:
        d = asdict(self)
        if self.images:  # imagens nao entram no cache; guardamos so o aviso
            d["meta"] = {**d["meta"], "tinha_imagens": len(self.images)}
        d["images"] = []
        return d

    @staticmethod
    def from_json(d: dict) -> "FileItem":
        return FileItem(**d)


@dataclass
class AttachmentResult:
    """Resultado da leitura de um anexo da entrega (pode gerar varios arquivos)."""

    idx: int
    title: str
    origin: str               # drive | link | youtube | form
    url: str = ""
    files: list = field(default_factory=list)   # list[FileItem]
    note: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)

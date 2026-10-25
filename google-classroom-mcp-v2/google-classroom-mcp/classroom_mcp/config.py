"""Configuracoes compartilhadas entre auth.py e o servidor MCP."""

import os
from pathlib import Path

# Os escopos sao os mesmos da versao anterior, para nao exigir nova autorizacao.
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/classroom.profile.emails",
    "https://www.googleapis.com/auth/classroom.announcements",
    "https://www.googleapis.com/auth/drive.readonly",
]

BASE_DIR = Path(os.environ.get("CLASSROOM_MCP_HOME", str(Path.home() / ".classroom-mcp")))
TOKEN_PATH = BASE_DIR / "token.json"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
DB_PATH = BASE_DIR / "classroom.db"
CACHE_DIR = BASE_DIR / "cache"
EXPORT_DIR = BASE_DIR / "exports"

# Token do GitHub: de preferencia por variavel de ambiente, para nao passar pela conversa.
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

# Limites de leitura
MAX_DRIVE_BYTES = 15_000_000          # arquivo individual do Drive
MAX_ZIP_UNCOMPRESSED = 30_000_000     # soma dos arquivos de um zip
MAX_ZIP_MEMBERS = 200
MAX_FOLDER_FILES = 40                 # arquivos lidos de uma pasta do Drive
MAX_GITHUB_FILE_BYTES = 150_000
MAX_GITHUB_TOTAL_BYTES = 1_500_000    # lido do repositorio (o que vai para a conversa e menor)
MAX_NOTEBOOK_OUTPUT_CHARS = 1500      # por saida de celula
CACHE_TTL_SECONDS = 12 * 3600

# Limites do que volta para a conversa (em caracteres)
DEFAULT_OUTPUT_BUDGET = 60_000
DEFAULT_CHARS_PER_FILE = 15_000
MAX_IMAGES_PER_CALL = 4
MAX_IMAGE_BYTES = 2_000_000

CODE_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue",
    ".py", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".php",
    ".rb", ".go", ".rs", ".swift", ".kt", ".sh", ".bash", ".bat", ".ps1",
    ".sql", ".r", ".m", ".scala", ".dart",
}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".csv", ".tsv", ".tex", ".log", ".ipynb",
}
SPECIAL_FILENAMES = {".gitignore", ".env.example", "dockerfile", "makefile", "requirements.txt"}

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", "vendor", ".cache", "coverage", ".idea", ".vscode", ".ipynb_checkpoints",
    "target", "bin", "obj",
}
SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
    "composer.lock", "gemfile.lock", "cargo.lock",
}

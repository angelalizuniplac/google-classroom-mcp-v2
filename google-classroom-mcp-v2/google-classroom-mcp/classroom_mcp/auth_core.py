"""Logica de credenciais compartilhada por auth.py e pelo servidor."""

import os
import stat

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from .config import BASE_DIR, CREDENTIALS_PATH, SCOPES, TOKEN_PATH

# Permite que o Google devolva escopos ligeiramente diferentes dos solicitados.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class AuthError(Exception):
    """Erro de autenticacao com mensagem pronta para o usuario."""


def save_token(creds: Credentials) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    try:
        TOKEN_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass  # Windows


def load_credentials(interactive: bool = False) -> Credentials:
    """Carrega o token salvo, renovando se preciso.

    interactive=False (servidor MCP): nunca abre o browser; orienta a rodar auth.py.
    interactive=True (auth.py): abre o fluxo OAuth quando nao ha token valido.
    """
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_token(creds)
            return creds
        except Exception as e:  # invalid_scope, invalid_grant, revogado
            TOKEN_PATH.unlink(missing_ok=True)
            creds = None
            if not interactive:
                raise AuthError(
                    "O token salvo nao pode ser renovado (%s). Ele foi apagado. "
                    "Rode `python auth.py` para autenticar de novo." % e
                )

    if not interactive:
        raise AuthError(
            "Ainda nao ha token valido. Rode `python auth.py` uma vez, no terminal, "
            "dentro da pasta do projeto, e depois reinicie o Claude Desktop."
        )

    if not CREDENTIALS_PATH.exists():
        raise AuthError(
            "credentials.json nao encontrado em %s. Veja o passo 2 do README." % CREDENTIALS_PATH
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    save_token(creds)
    return creds

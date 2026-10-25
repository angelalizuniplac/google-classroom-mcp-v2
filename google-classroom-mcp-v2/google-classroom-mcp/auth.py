#!/usr/bin/env python3
"""
Execute este script UMA VEZ para autenticar com o Google.
Ele abre o browser, pede login e salva o token em ~/.classroom-mcp/token.json
"""

from googleapiclient.discovery import build

from classroom_mcp.auth_core import AuthError, load_credentials
from classroom_mcp.config import TOKEN_PATH


def main() -> None:
    try:
        print("Verificando credenciais...")
        creds = load_credentials(interactive=True)
        print("Token salvo em: %s" % TOKEN_PATH)
    except AuthError as e:
        print("ERRO: %s" % e)
        return

    print("\nTestando conexao com o Google Classroom...")
    try:
        service = build("classroom", "v1", credentials=creds)
        me = service.userProfiles().get(userId="me").execute()
        print("Autenticado como: %s (%s)" % (me.get("name", {}).get("fullName"), me.get("emailAddress")))

        turmas, token = [], None
        while True:
            res = service.courses().list(
                teacherId="me", courseStates=["ACTIVE"], pageToken=token
            ).execute()
            turmas += res.get("courses", [])
            token = res.get("nextPageToken")
            if not token:
                break
        print("Turmas encontradas: %d" % len(turmas))
        for t in turmas:
            print("   - %s (ID: %s)" % (t.get("name"), t.get("id")))
        print("\nTudo certo. Agora configure o Claude Desktop conforme o README e reinicie.")
    except Exception as e:
        print("\nErro ao testar conexao: %s" % e)
        print("Verifique se as APIs Google Classroom e Google Drive estao habilitadas no console.")


if __name__ == "__main__":
    main()

"""Teste de integracao: o servidor MCP inteiro contra um Classroom/Drive simulado."""

import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from classroom_mcp import cache, drive_reader, store, tools
from classroom_mcp import classroom_api

CODE_A = '''
def media(valores):
    total = 0
    for v in valores:
        total += v
    return total / len(valores)

def maior(valores):
    m = valores[0]
    for v in valores:
        if v > m:
            m = v
    return m

def ordena(valores):
    resultado = sorted(valores)
    return resultado

def contagem(texto):
    freq = {}
    for palavra in texto.split():
        freq[palavra] = freq.get(palavra, 0) + 1
    return freq
'''
CODE_B = CODE_A.replace("valores", "lista").replace("total", "soma").replace("freq", "contador")
CODE_C = '''
import random

class Jogo:
    def __init__(self, nome):
        self.nome = nome
        self.pontos = 0

    def jogar(self):
        dado = random.randint(1, 6)
        if dado > 3:
            self.pontos += dado
        return self.pontos
'''
NOTEBOOK = json.dumps({"cells": [
    {"cell_type": "markdown", "source": "# Analise", "metadata": {}},
    {"cell_type": "code", "source": CODE_C, "execution_count": 1, "metadata": {},
     "outputs": [{"output_type": "error", "ename": "NameError", "evalue": "x", "traceback": []}]}]})

FILES = {"f1": ("main.py", CODE_A), "f2": ("main.py", CODE_B), "f3": ("analise.ipynb", NOTEBOOK)}


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"erro")


class Rec:
    patches, announcements, returns = [], [], []
    fail_announcement = False
    fail_patch = False


class FakeCS:
    subs = [
        {"id": "s1", "userId": "u1", "state": "TURNED_IN", "updateTime": "2026-10-20T10:00:00Z",
         "assignmentSubmission": {"attachments": [{"driveFile": {"id": "f1", "title": "main.py", "alternateLink": "x"}}]}},
        {"id": "s2", "userId": "u2", "state": "TURNED_IN", "late": True, "updateTime": "2026-10-21T10:00:00Z",
         "assignmentSubmission": {"attachments": [{"driveFile": {"id": "f2", "title": "main.py", "alternateLink": "x"}}]}},
        {"id": "s3", "userId": "u3", "state": "TURNED_IN", "updateTime": "2026-10-20T11:00:00Z", "draftGrade": 5,
         "assignmentSubmission": {"attachments": [{"driveFile": {"id": "f3", "title": "analise.ipynb", "alternateLink": "x"}}]}},
        {"id": "s4", "userId": "u4", "state": "NEW", "updateTime": "2026-10-20T11:00:00Z"},
    ]
    work = {"id": "W1", "title": "Atividade 7", "maxPoints": 10, "workType": "ASSIGNMENT", "state": "PUBLISHED",
            "description": "Implemente funcoes de media e maior.", "dueDate": {"year": 2026, "month": 10, "day": 20},
            "dueTime": {"hours": 23, "minutes": 59}}

    def courses(self):
        cs = self

        class Courses:
            def list(self, **kw):
                return Req(lambda: {"courses": [{"id": "C1", "name": "Ciencia de Dados", "section": "8o", "alternateLink": "l"}]})

            def students(self):
                class St:
                    def list(self, **kw):
                        return Req(lambda: {"students": [
                            {"userId": "u1", "profile": {"name": {"fullName": "Ana Souza"}, "emailAddress": "ana@x.com"}},
                            {"userId": "u2", "profile": {"name": {"fullName": "Bia Lima"}, "emailAddress": "bia@x.com"}},
                            {"userId": "u3", "profile": {"name": {"fullName": "Caio Reis"}, "emailAddress": "caio@x.com"}},
                            {"userId": "u4", "profile": {"name": {"fullName": "Dani Alves"}, "emailAddress": "dani@x.com"}}]})
                return St()

            def courseWork(self):
                class CW:
                    def get(self, courseId, id):
                        return Req(lambda: cs.work)

                    def list(self, **kw):
                        return Req(lambda: {"courseWork": [cs.work]})

                    def studentSubmissions(self):
                        class SS:
                            def list(self, **kw):
                                return Req(lambda: {"studentSubmissions": cs.subs})

                            def get(self, courseId, courseWorkId, id):
                                return Req(lambda: next(s for s in cs.subs if s["id"] == id))

                            def patch(self, courseId, courseWorkId, id, updateMask, body):
                                def go():
                                    if Rec.fail_patch:
                                        raise http_error(403)
                                    Rec.patches.append((id, updateMask, body))
                                    return {}
                                return Req(go)

                            def return_(self, courseId, courseWorkId, id, body):
                                return Req(lambda: Rec.returns.append(id) or {})
                        return SS()
                return CW()

            def announcements(self):
                class An:
                    def create(self, courseId, body):
                        def go():
                            if Rec.fail_announcement:
                                raise http_error(403)
                            Rec.announcements.append(body)
                            return {}
                        return Req(go)
                return An()
        return Courses()

    def userProfiles(self):
        class UP:
            def get(self, userId):
                return Req(lambda: {"name": {"fullName": "Profa Angela"}, "emailAddress": "angela@x.com"})
        return UP()


class FakeDS:
    def files(self):
        class F:
            def get(self, fileId, fields=None, supportsAllDrives=None):
                name, _ = FILES[fileId]
                mime = "application/vnd.google.colaboratory" if name.endswith(".ipynb") else "text/x-python"
                return Req(lambda: {"id": fileId, "name": name, "mimeType": mime, "size": "500",
                                    "modifiedTime": "2026-10-20T09:00:00Z"})
        return F()

    def revisions(self):
        class R:
            def list(self, fileId, **kw):
                def go():
                    raise http_error(403)
                return Req(go)
        return R()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(store, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tools, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(classroom_api, "_roster_cache", {})
    cs, ds = FakeCS(), FakeDS()
    monkeypatch.setattr(tools, "get_services", lambda: (cs, ds))
    monkeypatch.setattr(drive_reader, "_download", lambda ds_, fid, export=None: FILES[fid][1].encode())
    tools._att_cache.clear()
    Rec.patches, Rec.announcements, Rec.returns = [], [], []
    Rec.fail_announcement = Rec.fail_patch = False
    yield


async def call(name, **args):
    res = await tools.mcp.call_tool(name, args)
    blocks = res[0] if isinstance(res, tuple) else res
    return "\n".join(getattr(b, "text", "[imagem]") for b in blocks)


IDS = {"courseId": "C1", "courseWorkId": "W1"}


async def test_ferramentas_registradas_e_descricoes_curtas():
    ts = await tools.mcp.list_tools()
    names = {t.name for t in ts}
    assert {"triagem_turma", "analisar_plagio", "ler_arquivo", "publicar_feedbacks", "sinais_de_autoria"} <= names
    total = sum(len(t.description or "") + len(json.dumps(t.inputSchema)) for t in ts)
    print("tokens aproximados das definicoes:", total // 4, "em", len(ts), "ferramentas")
    assert total // 4 < 9000


async def test_turmas_atividade_e_listagem():
    assert "Ciencia de Dados" in await call("listar_turmas")
    ativ = await call("obter_atividade", **IDS)
    assert "Implemente funcoes" in ativ and "prazo 20/10/2026" in ativ
    todas = await call("listar_entregas", **IDS)
    assert "Ana Souza" in todas and "ATRASADA" in todas and "id=s1" in todas
    assert "Dani" not in await call("listar_entregas", filtro="entregues", **IDS)
    assert "Ana Souza" in await call("listar_entregas", filtro="sem_nota", **IDS)
    assert "Caio" not in await call("listar_entregas", filtro="sem_nota", **IDS)
    assert "Bia" in await call("listar_entregas", filtro="atrasadas", **IDS)
    assert "Dani" in await call("listar_entregas", filtro="nao_entregues", **IDS)


async def test_proxima_pendente_pula_quem_tem_nota():
    r = await call("proxima_entrega_pendente", **IDS)
    assert "Ana Souza" in r and "Restam 2" in r


async def test_resumir_e_ler_por_nome():
    r = await call("resumir_entrega", nome="bia", **IDS)
    assert "main.py" in r and "tokens" in r
    lido = await call("ler_entrega_aluno", nome="ana", **IDS)
    assert "CONTEUDO_DO_ALUNO" in lido and "def media" in lido and "AVISO DE SEGURANCA" in lido
    parte = await call("ler_arquivo", nome="ana", offset=0, limite=200, **IDS)
    assert "cortado" in parte and "offset=" in parte
    assert "Nenhum aluno" in await call("resumir_entrega", nome="zzz", **IDS)


async def test_orcamento_corta_e_lista_o_que_ficou():
    from classroom_mcp import render
    from classroom_mcp.models import AttachmentResult, FileItem
    big = FileItem(name="grande.py", kind="code", text="x = 1\n" * 5000)
    small = FileItem(name="pequeno.py", kind="code", text="y = 2\n")
    other = FileItem(name="outro.py", kind="code", text="z = 3\n" * 2000)
    att = AttachmentResult(idx=1, title="repo", origin="link", files=[big, small, other])
    blocks = render.render_entrega("H", [att], budget=3000, per_file=2500)
    out = "\n".join(getattr(b, "text", "") for b in blocks)
    assert "cortado" in out and "ler_arquivo" in out
    assert "outro.py" in out.split("Nao incluidos")[-1]  # o que nao coube e listado
    assert len(out) < 8000


async def test_triagem_e_plagio():
    t = await call("triagem_turma", **IDS)
    assert "ALERTA" in t and "Ana Souza" in t and "notebook com 1 erro" in t
    p = await call("analisar_plagio", **IDS)
    assert "PARES SUSPEITOS" in p and "Ana Souza" in p and "Bia Lima" in p
    d = await call("analisar_plagio", duplas=[["Ana", "Bia"]], salvar_historico=False, **IDS)
    assert "PARES SUSPEITOS" not in d and "DUPLAS DECLARADAS" in d


async def test_plagio_salva_historico():
    await call("analisar_plagio", **IDS)
    assert store.load_history("outra", "outra")


async def test_boas_praticas_e_sinais():
    r = await call("checar_boas_praticas", nome="caio", **IDS)
    assert "analise.ipynb" in r and "erro salvo" in r
    s = await call("sinais_de_autoria", nome="bia", **IDS)
    assert "atrasada" in s and "historico de revisoes indisponivel" in s and "Perguntas" in s


async def test_rubrica_e_validacao_de_nota():
    r = await call("salvar_rubrica", criterios=[{"nome": "Funcoes", "pontos": 6}, {"nome": "Qualidade", "pontos": 4}], **IDS)
    assert "10 pts" in r
    assert "Funcoes (6 pts)" in await call("obter_rubrica", **IDS)
    assert "maior que a pontuacao" in await call("preparar_feedback", nome="ana", feedback="x", nota=11, **IDS)
    assert "nao existe na rubrica" in await call("preparar_feedback", nome="ana", feedback="x", criterios=[{"nome": "Outro", "pontos": 1}], **IDS)
    assert "excede" in await call("preparar_feedback", nome="ana", feedback="x", criterios=[{"nome": "Funcoes", "pontos": 7}], **IDS)
    assert "nao confere" in await call("preparar_feedback", nome="ana", feedback="x", nota=9,
                                       criterios=[{"nome": "Funcoes", "pontos": 5}], **IDS)
    ok = await call("preparar_feedback", nome="ana", feedback="Bom trabalho.", criterios=[
        {"nome": "Funcoes", "pontos": 5, "comentario": "faltou maior"}, {"nome": "Qualidade", "pontos": 4}], **IDS)
    assert "nota 9" in ok


async def test_publicacao_exige_confirmacao_e_nunca_vai_para_a_turma():
    await call("preparar_feedback", nome="ana", feedback="Bom trabalho.", nota=8, **IDS)
    previa = await call("publicar_feedbacks", **IDS)
    assert "PREVIA" in previa and not Rec.patches and not Rec.announcements

    Rec.fail_announcement = True
    falha = await call("publicar_feedbacks", confirmar=True, **IDS)
    assert "FEEDBACK NAO ENVIADO" in falha and "Nada foi publicado para a turma" in falha
    assert Rec.announcements == []  # sem fallback para o mural geral

    Rec.fail_announcement = False
    ok = await call("publicar_feedbacks", confirmar=True, devolver=True, **IDS)
    assert "PUBLICADO" in ok
    assert Rec.patches[-1][1] == "draftGrade,assignedGrade" and Rec.patches[-1][2]["assignedGrade"] == 8
    body = Rec.announcements[0]
    assert body["assigneeMode"] == "INDIVIDUAL_STUDENTS" and body["individualStudentsOptions"]["studentIds"] == ["u1"]
    assert Rec.returns == ["s1"]


async def test_nota_recusada_pelo_google_e_explicada():
    await call("preparar_feedback", nome="ana", feedback="ok", nota=8, **IDS)
    Rec.fail_patch = True
    r = await call("publicar_feedbacks", confirmar=True, canal="somente_nota", **IDS)
    assert "NOTA NAO SALVA" in r and "projeto do Google Cloud" in r


async def test_nao_devolve_entrega_nao_entregue():
    r = await call("enviar_feedback_aluno", nome="dani", feedback="Nao entregou.", nota=0, confirmar=True, devolver=True, **IDS)
    assert "nao devolvida" in r or "nao devolvida".lower() in r.lower()
    assert Rec.returns == []


async def test_exportacoes():
    await call("preparar_feedback", nome="ana", feedback="Bom.", nota=8, **IDS)
    e = await call("exportar_feedbacks", **IDS)
    assert "feedbacks_W1.md" in e and "Bom." not in e  # nao devolve o texto, so o caminho
    n = await call("exportar_notas", **IDS)
    assert "4 aluno(s)" in n and "3 entregue(s)" in n

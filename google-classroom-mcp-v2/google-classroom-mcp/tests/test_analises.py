import json

from classroom_mcp.boas_praticas import analyze_item, analyze_python_source, analyze_html
from classroom_mcp.filetypes import read_bytes
from classroom_mcp.plagio import analyze, docs_from_items, format_report
from classroom_mcp.sinais import content_signals, entrega_signals, oral_questions, parse_ts, due_datetime

BASE_CODE = '''
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

def principal():
    dados = [3, 1, 2, 9, 4]
    print(media(dados), maior(dados), ordena(dados))
    print(contagem("a b a c b a"))
'''

RENAMED = BASE_CODE.replace("valores", "lista").replace("total", "soma").replace("resultado", "res").replace("freq", "contador")

OTHER = '''
import random

class Jogo:
    def __init__(self, nome):
        self.nome = nome
        self.pontos = 0

    def jogar(self):
        dado = random.randint(1, 6)
        if dado > 3:
            self.pontos += dado
        else:
            self.pontos -= 1
        return self.pontos

def executar():
    j = Jogo("ana")
    for _ in range(10):
        j.jogar()
    print(j.nome, j.pontos)
'''


def item(name, text):
    return read_bytes(name, text.encode())[0]


def build(students):
    return {n: docs_from_items(n, [item("main.py", t)]) for n, t in students.items()}


def test_copia_com_renomeacao_e_detectada_e_diferente_nao():
    r = analyze(build({"Ana": BASE_CODE, "Bia": RENAMED, "Caio": OTHER}), [], limiar=0.7)
    assert [(p["a"], p["b"]) for p in r["suspeitos"]] == [("Ana", "Bia")]
    ana_bia = r["suspeitos"][0]
    assert ana_bia["estrutural"] > 0.9
    assert ana_bia["evidencias"], "deve mostrar trechos coincidentes"
    caio = [p for p in r["pares"] if "Caio" in (p["a"], p["b"])]
    assert all(p["score"] < 0.4 for p in caio)


def test_baseline_do_professor_nao_gera_suspeita():
    # os dois alunos so mantem o codigo inicial do professor
    students = build({"Ana": BASE_CODE, "Bia": BASE_CODE + OTHER.replace("Jogo", "Dado")})
    baseline = docs_from_items("prof", [item("modelo.py", BASE_CODE)])
    r = analyze(students, baseline, limiar=0.7)
    assert not r["suspeitos"]


def test_dupla_declarada_nao_e_suspeita():
    r = analyze(build({"Ana": BASE_CODE, "Bia": BASE_CODE}), [], limiar=0.7, dupla_pairs={frozenset(("Ana", "Bia"))})
    assert not r["suspeitos"] and len(r["duplas"]) == 1


def test_comentarios_e_espacos_nao_enganam():
    com = "\n".join(l + ("  # explicacao %d\n# comentario extra" % i if i % 2 == 0 else "")
                    for i, l in enumerate(BASE_CODE.splitlines()))
    r = analyze(build({"Ana": BASE_CODE, "Bia": com + "\n# fim\n"}), [], limiar=0.7)
    assert r["suspeitos"]


def test_texto_curto_e_inconclusivo():
    r = analyze(build({"Ana": "x = 1\n", "Bia": "x = 1\n"}), [], limiar=0.7)
    assert not r["suspeitos"]


def test_historico_detecta_trabalho_antigo():
    r1 = analyze(build({"Ana": BASE_CODE, "Caio": OTHER}), [], limiar=0.7)
    fp = r1["fingerprints"]["Ana"]["code"]
    hist = [{"kind": "code", "lit": fp["lit"], "est": fp["est"], "label": "2026.1", "name": "Aluno Antigo"}]
    r2 = analyze(build({"Bia": RENAMED, "Caio": OTHER}), [], limiar=0.7, history=hist)
    assert r2["historico"] and r2["historico"][0]["aluno"] == "Bia"


def test_relatorio_formata():
    r = analyze(build({"Ana": BASE_CODE, "Bia": RENAMED, "Caio": OTHER}), [], limiar=0.7)
    txt = format_report(r)
    assert "PARES SUSPEITOS" in txt and "Ana" in txt and "nao prova copia" in txt


def test_notebook_compara_so_codigo():
    def nb(src, out):
        return json.dumps({"cells": [{"cell_type": "code", "source": src, "execution_count": 1,
                                      "outputs": [{"output_type": "stream", "name": "stdout", "text": out}], "metadata": {}}]}).encode()
    a = read_bytes("a.ipynb", nb(BASE_CODE, "resultado A"))[0]
    b = read_bytes("b.ipynb", nb(RENAMED, "saida totalmente diferente"))[0]
    r = analyze({"Ana": docs_from_items("Ana", [a]), "Bia": docs_from_items("Bia", [b])}, [], limiar=0.7)
    assert r["suspeitos"]


# ---------------- boas praticas ----------------

def test_python_achados():
    code = "import os, sys\n\ndef f(a, b=[]):\n    try:\n        x = 1\n    except:\n        pass\n    return a\n"
    r = analyze_python_source(code)
    txt = " ".join(r["achados"])
    assert "except sem tipo" in txt and "mutavel" in txt and "imports nao usados" in txt


def test_python_erro_de_sintaxe():
    assert analyze_python_source("def f(:\n")["erro_sintaxe"]


def test_html_achados():
    a = analyze_html("<html><body><img src='a.png'><font>x</font></body></html>")
    joined = " ".join(a)
    assert "DOCTYPE" in joined and "alt" in joined and "lang" in joined


def test_analyze_item_notebook_com_erro():
    nb = json.dumps({"cells": [{"cell_type": "code", "source": "1/0", "execution_count": 1, "metadata": {},
                                "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "x", "traceback": []}]}]}).encode()
    res = analyze_item(read_bytes("n.ipynb", nb)[0], use_ruff=False)
    assert any("erro salvo" in a for a in res["achados"])


# ---------------- sinais ----------------

def test_parse_ts_e_prazo():
    assert parse_ts("2026-10-20T14:03:11.123Z").hour == 14
    assert parse_ts("2026-10-20T14:03:11Z").minute == 3
    d = due_datetime({"dueDate": {"year": 2026, "month": 10, "day": 20}, "dueTime": {"hours": 23, "minutes": 59}})
    assert d.day == 20


def test_sinais_de_entrega():
    sub = {"late": True, "submissionHistory": [
        {"stateHistory": {"state": "TURNED_IN", "stateTimestamp": "2026-10-20T23:30:00Z"}},
        {"stateHistory": {"state": "RECLAIMED_BY_STUDENT", "stateTimestamp": "2026-10-20T23:40:00Z"}}]}
    work = {"dueDate": {"year": 2026, "month": 10, "day": 20}, "dueTime": {"hours": 23, "minutes": 59}}
    texts = " ".join(s["texto"] for s in entrega_signals(sub, work))
    assert "atrasada" in texts and "reenviou" in texts and "antes do prazo" in texts


def test_sinais_de_conteudo_e_perguntas():
    code = "\n".join('def f%d(x):\n    """Docstring completa."""\n    # Step %d: processa\n    return x' % (i, i) for i in range(6))
    code += '\n# Aqui esta o codigo. Espero que isso ajude!\n'
    it = item("main.py", code)
    sig = content_signals([it])
    assert any("chatbot" in s["texto"] for s in sig)
    assert any("docstring" in s["texto"] for s in sig)
    assert oral_questions([it])

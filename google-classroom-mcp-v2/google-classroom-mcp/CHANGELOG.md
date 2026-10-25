# Changelog

## 2.0.0

### Corrigido
- Listagens sem paginação (turmas, atividades, entregas) perdiam alunos em turmas grandes.
- `.ipynb` e `.csv` não eram lidos (arquivo entregue ou repositório GitHub).
- URLs do GitHub com `/` no fim, `/blob/` ou `?tab=` eram recusadas; branches com `/` não funcionavam.
- IDs em links do Classroom (base64) agora são convertidos para o ID da API.
- XLSX era lido como texto binário; `.doc` antigo gerava erro obscuro.
- `.gitignore` e `.env.example` nunca casavam na lista de extensões.
- `enviar_feedback_aluno` tentava um campo inexistente (`privateComment`) e, se o anúncio individual falhasse, **publicava o feedback com o nome do aluno para a turma inteira**. O fallback foi removido.
- Nota não era validada contra a pontuação da atividade; `return` era tentado em entregas não entregues.
- Chamadas ao Google bloqueavam o servidor; agora rodam fora do loop de eventos.
- Servidor abria o navegador no meio de uma ferramenta se o token faltasse; agora orienta a rodar `auth.py`.
- `requirements.txt` aceitava `mcp>=1.0.0`, que instala a versão 2 (incompatível). Agora `mcp>=1.2,<2`.

### Novo
- Leitura de notebooks, CSV, XLSX, ZIP, Colab, Docs, pastas do Drive, Gist, páginas web, PDF escaneado como imagem.
- `triagem_turma`, `resumir_entrega`, `ler_arquivo`, `proxima_entrega_pendente`.
- Plágio por trechos com enunciado/código inicial descontados, duplas, renomeação de variáveis, evidências e histórico entre turmas.
- `sinais_de_autoria` e `checar_boas_praticas`.
- Rubrica local, rascunhos de feedback, prévia e `confirmar=true`, `exportar_feedbacks`, `exportar_notas`.
- Defesa contra instruções escondidas nos trabalhos e bloqueio de SSRF.
- Cache em disco, orçamento de tokens por chamada, imagens reduzidas, texto compactado.

### Alterado
- `PyPDF2` substituído por `pypdf`.
- `server.py` virou ponto de entrada; o código está em `classroom_mcp/`.
- `GITHUB_TOKEN` por variável de ambiente (o parâmetro `github_token` continua aceito).

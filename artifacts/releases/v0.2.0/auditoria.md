# Auditoria da versão v0.2.0

Data: 2026-09-21

## Escopo auditado

- CLI `agent-bench` e serviço da bancada.
- Hub MCP e ferramentas de diagnóstico.
- Localização e abertura do Chromium.
- Visita humana, retomada e fechamento do viewer.
- Documentação operacional em português e inglês.

## Resultado

O fluxo de navegador ficou restrito ao perfil persistente da bancada nomeada. Antes de devolver um endpoint CDP, a implementação confere o processo, seu cgroup e a raiz do perfil. Pedidos com `--isolado` são rejeitados porque criariam uma identidade vazia; não existe código de cópia de cookies ou fallback para o navegador humano.

O supervisor passa a devolver estado de entrada confirmado e pressão do cgroup. Quando não consegue criar uma thread por falta de tarefas, atende a solicitação de controle de forma síncrona para preservar diagnóstico e recuperação.

## Evidências

- `python3 -m unittest discover -s desktop -p 'test_*.py'`: 53 testes, todos aprovados.
- `python3 -m py_compile bin/agent-bench desktop/*.py`: aprovado.
- `git diff --check`: aprovado.

## Limitações conhecidas

- A versão não prepara credenciais de navegador. O perfil `Default` deve existir previamente dentro da bancada autorizada.
- Bancadas em execução não são reiniciadas automaticamente durante a atualização para evitar interromper trabalho em andamento.

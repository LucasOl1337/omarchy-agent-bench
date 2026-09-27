# Auditoria da versão v0.3.0

Data: 2026-09-27

## Base e candidato

- Base oficial: GitHub Release `v0.2.0`, tag anotada `41e96a5` apontando pra `04f213f` (merge do PR #1 na `main`).
- `origin/main` antes do release: `04f213f` (igual à base). É ancestral do candidato, sem divergência.
- Branch de trabalho: `consolida/2026-09-23` em `742e664`, limpa e em dia com o upstream, 12 commits à frente da `main`, com PR #2 aberto desde 23/09.
- Candidato: `742e664` + commit de release (fila pesada, docs, versão, artefatos). Integração na `main` pelo merge do PR #2, seguindo a convenção do PR #1.

## Matriz de mudanças

| Mudança | Commits / arquivos | Situação | Destino |
| --- | --- | --- | --- |
| Porta CDP fixa + GPU real (Cloudflare) | 3219f81, `bench_ops.py` | integrada | notas, Correções |
| Trava exclusiva de abertura do Chromium; título do processo não bloqueia CDP | 3219f81 | integrada | notas, Correções |
| Perfis: cofre persistente, novas bancadas `.efemero`, sem cópia da semente | 3219f81, `bench_ops.py`, `bench_profile.py` | integrada | notas, Sistemas |
| ExecStop fecha Chromium antes do Xvnc | 3219f81, `contrib/systemd/agent-bench@.service` | integrada | notas, Correções |
| `bar.json` pra barra do Omarchy; `visit` em workspace compartilhado | 3219f81, `bench_views.py`, `bench_human.py` | integrada | notas, Melhorias |
| `install.sh` com prefixo `~/.agents` não substitui o hub | 3219f81 | integrada | notas, Correções |
| `bench_web` / `agent-bench-web`, `agent-bench-profile`, hub MCP, testes | 3219f81 (trabalho não commitado de vários agentes desde v0.2.0) | integrada | notas, Novidades |
| Chromium convencional com controle nativo | e746b8d, 90c32b4, f3c7afe | integrada, opt-in | notas, Novidades |
| Validação Jcode | 1f2e6e8, 084ee21 | só docs | notas, Sistemas (Documentação) |
| Reaper por CPU nativa, 25 min | 71d3e1b, 3219f81 | integrada | notas, Melhorias + Correções |
| MCP publica núcleo de 20 ferramentas por padrão | 1930050, 36ab247 | integrada | notas, Melhorias |
| `origins.json`: agente, projeto, lançador | b8dd8e8, 3137437 | integrada; bancadas rodando só pegam no próximo start | notas, Novidades |
| cua-driver 0.29 `install_extension` recusado | 742e664 | integrada | notas, Sistemas |
| Fila pesada `agent-bench fila` + drop-ins de CPU | commit de release; trazido da árvore viva `~/.agents` (criado em 25/09, nunca commitado) | integrada neste release | notas, Novidades + Sistemas |
| README/`named-benches.md` diziam 3 h de ociosidade | commit de release | corrigida pra 25 min | só auditoria |
| Teste `CuaTimeouts` com prazo de 0,12 s falhava sob carga | commit de release, `test_bench_infra.py` (0,4 s, ainda abaixo do limite de 1 s do próprio teste) | corrigida | só auditoria |

## Cobertura de sessões

- **Claude:** encontrado. `~/.claude/projects/-home-lol`: `1dc37724` (23/09, consolidação e PR #2), `593aeeab` (24/09, origins.json), `8b87a8fc` (25/09, criou a fila pesada na árvore viva e a política), `27ec6642` (25/09, cua-driver 0.29 / install_extension), `ad3b5062` (25–26/09, uso da fila). Autoria Git é toda `LucasOl`; a atribuição vem do conteúdo das sessões.
- **Codex:** encontrado. Várias sessões em `~/.codex/sessions/2026/09/2x` citam o repo; `01a0d9b1` (25/09) usa a fila. O trabalho de 22/09 (native browser, Jcode) é compatível com Codex, confiança média; commits sem trailer.
- **Grok:** sessões em `~/.grok/sessions` citam o repo, anteriores à v0.2.0; nenhuma relacionada a esse intervalo confirmada.
- **Hermes, Pi, OpenCode:** nenhuma sessão relacionada encontrada nas fontes consultadas.
- **Orca:** fonte indisponível (sem diretório de histórico na máquina).

## Validação

- `python3 -m unittest discover -s desktop -p 'test_*.py'`: 426 testes (421 + 5 novos da fila). Passou limpo; sob load average 50+ na máquina (outros agentes), testes de prazo curto de `test_bench_bridge.DedicatedBridgeTimeouts` (0,16–0,2 s) falham de forma intermitente. Não tocados por este release; pré-existentes no candidato.
- `python3 -m py_compile bin/agent-bench bin/agent-bench-mcp desktop/*.py`: aprovado.
- `bash -n install.sh uninstall.sh`: aprovado.
- `git diff --check`: aprovado.
- Smoke da fila com `HOME` isolado: `fila`, `fila x rodar -- true`, `fila status` ok. O `systemctl set-property --runtime` do smoke criou um drop-in em `agent-bench@x.service` (unidade inexistente), removido em seguida.

## Exclusões e pendências

- `desktop/sessions/` e perfis vivos ficam fora do commit.
- `desktop/openbox.xml` da árvore viva só difere pelo `@PREFIX@` substituído pelo instalador; não é mudança.
- Ativação local: a árvore viva `~/.agents` é uma cópia (não symlink) e já roda esse código, inclusive a fila. `install.sh` não foi executado (AGENTS.md proíbe sem pedido do operador, e ele sobrescreve units e regras do Hyprland). Nenhuma bancada, serviço ou MCP foi reiniciado. `test_bench_origin.py` e docs novos existem só no repo; nada disso muda o runtime.
- Migrations e deploy: não aplicáveis (ferramenta local, sem serviço hospedado).

# omarchy-agent-bench

**Bancadas X11 aninhadas para agentes de IA no [Omarchy](https://omarchy.org) / Hyprland. Os workspaces 1–5 continuam seus.**

[English](README.md) · [Por quê](docs/why.md) · [Instalação](docs/install.md) · [Convivência](docs/coexistence.md) · [CDP e CUA](docs/cdp-and-cua.md) · [Arquitetura](docs/architecture.md)

Agentes de código no desktop pessoal pegam o mouse, abrem aba no Chromium humano e disparam `hyprctl dispatch workspace`. Aqui cada tarefa concorrente ganha uma **bancada nomeada**: display Xvnc, Openbox, perfil Chromium, D-Bus, clipboard e CDP próprios. O viewer TigerVNC fica num **workspace reservado (6–11)** para você acompanhar sem o agente puxar o ponteiro.

É isolamento de **tela e entrada**, não uma sandbox de segurança. O agente continua vendo seus arquivos.

## O que vem

| Peça | Função |
| --- | --- |
| `agent-bench` | CLI: `ensure`, `cdp`, `browser`, `exec`, `keep`, `gc`, … |
| `agent-bench-mcp` | Um MCP stdio para todos os harnesses: `bench_ensure`, `bench_cdp`, CUA com `bench=` |
| Workspaces **6–11** | Persistentes para os viewers. Nunca 12+ (o Hyprland abre esse número no monitor focado) |
| Workspaces **1–5** | Sessão humana. `hyprctl -j` só leitura. Sem grim, ydotool ou computer-use aí |
| Skills | Agent Skills: convivência, operação da bancada, receita de login persistente |

Um Xvnc vazio é barato (~80–140 MiB, ~0,3 s). O custo é o Chromium. Bancadas sobem sob demanda. Ociosas sem páginas reais são encerradas após 3 h (`padrao` e controle humano ficam). O perfil sobrevive ao `stop`; `agent-bench keep NOME` grava `.keep` para o GC de disco não apagar o login.

## Uso

Precisa de sessão Omarchy ou Hyprland, Python 3, systemd --user, TigerVNC (`Xvnc` + `vncviewer`), Openbox, Chromium, xdotool, xclip, xterm.

```sh
git clone https://github.com/LucasOl1337/omarchy-agent-bench.git
cd omarchy-agent-bench
./install.sh
```

O instalador copia para `~/.local/share/omarchy-agent-bench`, liga `~/.local/bin/agent-bench`, habilita `agent-bench-views.service` e acrescenta `require("hypr.agent-bench")` no `hyprland.lua` (com backup). **Não** reinicia bancadas que já estavam rodando.

```sh
agent-bench ensure demo
agent-bench cdp demo
agent-bench browser demo https://example.com
```

Acompanhe com **Super+6** … **Super+9**, **Super+0** (workspace 10) ou o painel **Bancada dos agentes**. Super+6 só visita (o agente continua). **Super+Alt+A** / **Assumir controle** para clicar e digitar; **Super+1** no mesmo monitor devolve sozinho. O clipboard permanece separado.

Ligue o harness no MCP multiplexado (`contrib/mcp/`). Formulários: CDP/Playwright no endpoint da bancada. CUA de pixels só quando a página for opaca. Não use `hyprctl dispatch workspace`. Não dê `stop` no meio de um formulário.

Copie `skills/` para o hub que o seu harness já carrega. Três skills: `human-agent-coexistence`, `agent-bench`, `named-login-bench`.

## O que isto não é

Não é VM nem container. Não foi validado para GPU, Wayland exclusivo ou jogos. Não é o repositório [omarchy-agents](https://github.com/LucasOl1337/omarchy-agents) (aquele é o painel da waybar).

```sh
cd desktop
python3 -m unittest discover -s . -p 'test_*.py'
```

MIT. Issues e PRs que ajudem outras pessoas no Omarchy a conviver com agentes são bem-vindos.

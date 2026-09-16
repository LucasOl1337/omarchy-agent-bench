# Bancadas de agentes — workspaces 6–11

Toda navegação e operação visual dos agentes acontece em uma bancada, inclusive CDP, Playwright e browsers embutidos. O harness continua no lugar; a bancada fornece display, teclado, mouse, clipboard e D-Bus próprios. Arquivos e APIs continuam acessíveis normalmente.

## Para o humano

Cada bancada ativa recebe uma janela num workspace **6–11**, todos dedicados a agentes. A bancada `padrao` inicia no login. Outras tarefas recebem o próximo livre; se os seis estiverem ocupados, o viewer compartilha um deles. Workspaces 1–5 pertencem ao humano. Não alocar 12+.

Use **Super+6** … **Super+9**, **Super+0** (10), ou o painel **Bancada dos agentes** para visitar as bancadas. O painel permite localizar também o workspace 11. O supervisor posiciona os viewers sem trocar seu workspace ativo.

Para clicar e digitar na bancada, abra **Bancada dos agentes** no lançador. Selecione a bancada e clique em **Assumir controle**, depois em **Mostrar tela**. Os comandos de entrada do agente ficam bloqueados até **Devolver ao agente**. A devolução desliga a entrada pelo viewer e retorna a janela ao workspace reservado.

O clipboard permanece separado em ambos os modos. Fechar o viewer mantém os aplicativos funcionando. `agent-bench view NOME` reabre o viewer sem abrir o painel.

## Para qualquer agente

O comando fica em `~/.local/bin/agent-bench` depois do `./install.sh` (ou no clone, `bin/agent-bench`). Nome próprio por tarefa concorrente.

```sh
agent-bench ensure codex-login
agent-bench cdp codex-login
agent-bench browser codex-login http://localhost:3000
agent-bench screenshot codex-login /caminho/absoluto/captura.png
agent-bench exec codex-login -- xdotool mousemove 400 300 click 1
printf '%s' 'conteudo de teste' | agent-bench clipboard codex-login --set
agent-bench list
agent-bench stop codex-login
agent-bench gc --apply
```

`exec` herda o diretório atual e espera no máximo 60 segundos. Use `launch` para aplicativos duradouros; logs em `desktop/sessions/NOME/session.log`. Formulários devem preferir CDP; CUA/visão fica para páginas opacas.

Nomes: até 40 caracteres, minúsculas, números e hífen. Login persistente: `agent-bench keep NOME` e a skill `named-login-bench`.

## CUA e MCP multiplexado

```sh
agent-bench-mcp                 # stdio: bench_ensure, bench_cdp, CUA com parâmetro bench
agent-bench mcp hermes-teste   # CUA de uma bancada só
```

A ponte aplica a trava de controle humano e o retorno do viewer. Não conecta ao daemon CUA humano. `bench_ensure` sobe a bancada sozinho (~0,3 s).

## Persistência e manutenção

- `agent-bench@padrao.service` inicia no login. `agent-bench-views.service` acompanha os viewers. Outras bancadas sobem sob demanda.
- O supervisor encerra bancadas ociosas (sem páginas reais e sem comando há 3 h), exceto `padrao` e as que estão sob controle humano.
- Limites: `MemoryHigh=4G`, `MemoryMax=6G`, `TasksMax=512`. `stop` encerra processos; o perfil permanece. `.keep` protege contra o GC de disco.
- Display, Xauthority, runtime e D-Bus distintos da sessão humana. Chromium usa `--password-store=basic`.
- Tela 1600×1000, renderização por software. VNC só em socket Unix 0600; sem porta de rede.
- Não é uma sandbox de segurança para programas maliciosos.

Inspeção: `journalctl --user -u agent-bench@NOME.service` e `desktop/sessions/NOME/session.log`.

## Controle

```sh
agent-bench collaborate NOME
agent-bench resume NOME
agent-bench dock NOME
```

Agentes só executam `resume` quando o humano pedir a devolução. Não use `hyprctl dispatch workspace`.

Regras Hyprland: `~/.config/hypr/agent-bench.lua`. Estado: `$XDG_RUNTIME_DIR/agent-bench/views.json`.

## Workspace ausente ou CUA indisponível

1. `agent-bench list` e `agent-bench status NOME`. Desktop ativo com `viewer_pid: null` indica perda do visualizador.
2. `journalctl --user -u agent-bench-views.service`. O supervisor recupera saídas com erro após três segundos. Fechamento normal permanece fechado; `agent-bench dock NOME` reabre.
3. Se o supervisor estiver parado: `systemctl --user start agent-bench-views.service`. Isso não reinicia os desktops.
4. Teste `agent-bench-mcp` (`bench_doctor` ou `get_screen_size`) ou `agent-bench mcp NOME`.

## Central

F1 abre a central, Super+Enter o terminal, Alt+Tab e Alt+F4 são internos à bancada.

```sh
agent-bench home NOME
agent-bench terminal NOME
agent-bench tools
agent-bench doctor NOME
```

## Higiene das abas

Ao concluir uma pesquisa ou salvar o comprovante, feche as abas próprias sem próxima ação. Reutilize a aba de busca e a aba da mesma URL. Preserve formulários e envios incertos. Não feche abas de outro agente ou do humano. Não repita um envio para reconstruir evidência.

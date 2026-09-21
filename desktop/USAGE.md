# Bancadas de agentes — workspaces 6–11

Toda navegação e operação visual dos agentes acontece em uma bancada, inclusive CDP, Playwright e browsers embutidos. O harness continua no lugar; a bancada fornece display, teclado, mouse, clipboard e D-Bus próprios. Arquivos e APIs continuam acessíveis normalmente.

## Para o humano

Cada bancada ativa recebe uma janela num workspace **6–11**, todos dedicados a agentes. A bancada `padrao` inicia no login. Outras tarefas recebem o próximo livre; se os seis estiverem ocupados, o viewer compartilha um deles. Workspaces 1–5 pertencem ao humano. A antiga reserva de jogos em 10/11 foi removida. Não alocar 12+.

Use **Super+6** … **Super+9**, **Super+0** (10), **Super+Ctrl+0** (11), ou o painel **Bancada dos agentes** para entrar nas bancadas. Super+6 no monitor focado troca *aquele* monitor para o workspace 6, foca a viewer e assume mouse/teclado (o agente pausa). **Super+W** na viewer encerra a bancada; numa janela comum continua fechando só a janela. **Super+Alt+A** (ou **Assumir controle** no painel) assume de novo se a visita não liberou a entrada. **Super+1** no monitor que está mostrando a bancada devolve sozinho: o workspace deixa de ficar visível, o supervisor desliga a entrada do viewer, recoloca a janela no workspace reservado e o agente segue. Não precisa clicar em Devolver se você saiu da bancada.

O supervisor posiciona os viewers sem trocar seu workspace ativo. Se você mover o workspace inteiro para outro monitor, esse monitor continua sendo sua escolha.

Para clicar e digitar sem o atalho, o painel **Bancada dos agentes** ainda tem **Assumir controle**, **Mostrar tela** e **Devolver ao agente**. Uma operação já em andamento precisa terminar antes da troca; o painel informa quando tentar novamente.

O clipboard permanece separado em ambos os modos. O mouse e teclado físicos só comandam a bancada quando você escolhe interagir com sua janela durante o modo humano. O agente usa a entrada virtual da bancada.

Fechar só a janela da viewer (pelo X, sem Super+W) mantém os aplicativos funcionando. Super+W na viewer chama `agent-bench stop`. A janela pode reaparecer no workspace reservado quando o agente voltar a interagir, ou quando você pedir para mostrá-la no painel. Para acompanhar sem abrir o painel, `agent-bench view NOME` cria o viewer silenciosamente.

## Para qualquer agente

O comando está em `/home/lol/.agents/bin/agent-bench`, com atalho `/home/lol/.local/bin/agent-bench`. Não é necessário instalar outro harness ou transferir a tarefa. Missões DailyWork: skill `dailywork-bancada` e `agent-bench ensure` nos nomes `dailywork-candidaturas` / `dailywork-campanhas`.

```sh
# Use nome próprio por tarefa concorrente.
agent-bench ensure codex-login
agent-bench cdp codex-login
agent-bench browser codex-login http://localhost:3000
agent-bench screenshot codex-login /caminho/absoluto/captura.png

# Após conferir a captura, enviar entrada apenas para essa bancada.
agent-bench exec codex-login -- xdotool mousemove 400 300 click 1
agent-bench exec codex-login -- xdotool type --clearmodifiers 'texto de teste'

# Clipboard da bancada, separado do clipboard humano.
printf '%s' 'conteudo de teste' | agent-bench clipboard codex-login --set
agent-bench clipboard codex-login

agent-bench list
agent-bench stop codex-login
agent-bench gc --apply
```

`exec` herda o diretório atual do comando e espera no máximo 60 segundos pelo processo. Ele retorna stdout, stderr e o código de saída. Use `launch` para aplicativos duradouros; os logs ficam em `desktop/sessions/NOME/session.log`. Não iniciar aplicativos GUI de longa duração com `exec`.

O comando `browser` usa um diretório Chromium persistente exclusivo por bancada: `desktop/sessions/NOME/chromium`, com `Default` já preparado para aquela bancada. `agent-bench` não cria perfil vazio e não copia cookies ou dados de outro navegador como fallback.

`agent-bench browser-status NOME` verifica o PID no `SingletonLock` desse diretório e sua bancada no cgroup, sem iniciar aplicativos nem enumerar abas. Exigir `lives_in == NOME` e `user_data_dir` exclusivo. `browser`/`cdp` recusam endpoint fora da bancada. `ensure` sem navegador devolve o estado sem iniciar um perfil.

A origem `~/.config/chromium` e o Brave pessoal permanecem fora da rota dos agentes. Não copiar bancos em uso, remover locks, mover janelas ou reiniciar o processo humano. Se o perfil necessário estiver ocupado no desktop humano, preserve o checkpoint e recuse a navegação. Dados e credenciais ficam fora dos repositórios.

As bancadas aceitam nomes de até 40 caracteres, com letras minúsculas, números e hífen. Escolha nomes próprios por tarefa. O servidor serializa comandos curtos de uma bancada; isso não substitui a propriedade exclusiva sobre o aplicativo durante uma sequência de testes.

## CUA e MCP multiplexado

```sh
agent-bench-mcp                 # stdio: bench_ensure, bench_cdp, CUA com parâmetro bench
agent-bench mcp hermes-teste   # CUA de uma bancada só
```

`agent-hub sync-policy` declara `agent-bench-mcp` em Cursor, Codex, Claude, OpenCode, Gemini e Hermes. A ponte aplica a trava de controle humano e o retorno do viewer a cada ferramenta de entrada. Não conecta ao daemon CUA humano. `bench_ensure` sobe a bancada sozinho (~0,3 s).

## Persistência e manutenção

- `agent-bench@padrao.service` inicia no login do usuário. `agent-bench-views.service` acompanha as bancadas durante a sessão gráfica. Outras bancadas iniciam sob demanda e devem ser encerradas pelo proprietário. O supervisor encerra bancadas ociosas (sem páginas reais e sem comando há 3 h), exceto `padrao` e as que estão sob controle humano.
- Cada bancada roda num grupo de processos do systemd com `MemoryHigh=4G`, `MemoryMax=6G` e `TasksMax=512`; `stop` encerra seus aplicativos e processos filhos. O perfil e os arquivos são preservados. `agent-bench keep NOME` grava `.keep` contra o GC de disco.
- Display, Xauthority, runtime e D-Bus são distintos da sessão humana. Após o Xvnc subir, `dbus-update-activation-environment` publica DISPLAY no D-Bus da bancada para diálogos e portais. Os perfis das bancadas usam `--password-store=basic` com diretórios privados e cookies convertidos no preparo; nenhum processo da bancada usa o D-Bus humano para acessar o keyring.
- A tela é 1600×1000 e renderiza por software. Jogos, apps exclusivos de Wayland e testes de aceleração de GPU não foram validados nessa bancada X11.
- O VNC escuta somente em socket Unix com modo 0600; não há porta VNC de rede. Clipboard fica bloqueado também no servidor. Eventos de teclado e mouse do viewer só são liberados no modo humano.
- Display, Xauthority, runtime e D-Bus são distintos da sessão humana. O acesso habitual a arquivos permanece; não é uma sandbox de segurança para programas maliciosos.

Inspeção de falhas: `journalctl --user -u agent-bench@NOME.service` e `desktop/sessions/NOME/session.log`. Se uma bancada estiver parada, a operação retorna erro; não tentar a mesma interação no display humano.

## Controle e compatibilidade

```sh
# Ações do humano, disponíveis também no painel.
agent-bench visit 6
agent-bench visit --here
agent-bench close-here
agent-bench collaborate NOME
agent-bench collaborate --here
agent-bench resume NOME

# Retorno explícito, sem mudar seu workspace ativo.
agent-bench dock NOME
```

Super+6… / `visit` entra e pausa o agente. Super+W / `close-here` encerra a bancada da viewer focada. `collaborate` / Super+Alt+A também pausa. Sair do workspace da bancada (Super+1) dispara `resume` sozinho; o painel **Devolver ao agente** continua disponível. Agentes só executam `resume` quando o humano pedir a devolução. `exec`, `launch`, `browser`, escrita de clipboard e o MCP já aplicam a trava e o retorno. Use `launch` para abrir aplicativos duradouros; rotinas de automação devem passar por `exec` ou pelo MCP. A trava não suspende processos arbitrários iniciados por fora nem ferramentas antigas já conectadas diretamente ao display. O programa de teste pode continuar processando arquivos ou requisições enquanto a entrada do agente está pausada.

Bancadas que já estavam em uso antes desta atualização permanecem funcionando. O CLI atualizado posiciona seus viewers, mas o modo de colaboração só fica disponível depois que o proprietário encerrar e iniciar a própria bancada. Não reiniciar bancadas de outras tarefas para atualizar. Clientes MCP já abertos também precisam ser reabertos pelo proprietário para carregar a ponte atualizada.

O gerenciador reserva o número enquanto a bancada estiver ativa. Ele só identifica e move viewers que abriu, por PID, classe e título. A configuração está em `/home/lol/.config/hypr/agent-bench.lua`; atribuições e estados estão em `/run/user/1000/agent-bench/views.json`. Se o acompanhamento estiver indisponível, novos comandos de entrada retornam erro. Consulte `journalctl --user -u agent-bench-views.service`.

Cada bancada é um cgroup com `TasksMax=4096` (era 512; em 18/09/2026 uma missão DailyWork com muitas abas chegou a 512, o kernel passou a rejeitar fork, o Chromium morreu com SIGILL e o servidor da bancada parou de responder, inclusive ao Super+Alt+A). `agent-bench status NOME` mostra `tasks.current/max`; acima de 90% feche abas ou reinicie a bancada. O Chromium da bancada sobe com `--renderer-process-limit=24`. Quando a troca de controle falha, a mensagem diz o motivo e a bancada só fica em `humano` se o servidor confirmou que a entrada mudou. Se o fcitx5 do Omarchy reiniciar em loop (`systemctl --user status omarchy-fcitx5`), o teclado em janelas X11, o viewer incluído, fica instável: pare o `dbus-:1.1-org.fcitx.Fcitx5@0.service` que roubou o nome.


## Workspace ausente ou CUA indisponível

1. Execute `agent-bench list` e `agent-bench status NOME`. Desktop ativo com `viewer_pid: null` indica perda do visualizador; os aplicativos podem continuar funcionando.
2. Confira `journalctl --user -u agent-bench-views.service` e `desktop/sessions/NOME/viewer.log`. O supervisor recupera saídas com erro ou sinal após três segundos. Fechamento normal permanece fechado; `agent-bench dock NOME` solicita reabertura.
3. Se o supervisor estiver parado, inicie `systemctl --user start agent-bench-views.service`. Reiniciar só esse serviço recupera viewers sem reiniciar os desktops. Preserve bancadas de outras tarefas.
4. Teste `agent-bench-mcp` (ferramenta `bench_doctor` ou `get_screen_size` via CUA) ou `agent-bench mcp NOME` com um cliente MCP e consulte `get_screen_size`. Ausência de ferramentas nativas no harness e `cua-driver status` sem daemon global não significam falha desse MCP.

Em 15/09/2026, viewers terminaram com “X I/O error”, deixando `launched=true` e impedindo a recuperação. `bench_views.py` passou a distinguir falha de fechamento normal. Os testes em `test_bench_views_recovery.py` e uma falha provocada numa bancada de diagnóstico confirmaram recuperação com desktop e workspace humano preservados.

## Central e ferramentas

A tela inicial identifica a tarefa e o controle atual. Oferece navegador com perfil próprio, terminal, arquivos, guia, catálogo de ferramentas e diagnóstico. A aba Teste permite verificar clique, digitação e arraste sem usar aplicativos reais.

```sh
agent-bench home NOME        # abrir a central na bancada existente
agent-bench terminal NOME    # terminal isolado, no diretório atual
agent-bench tools            # catálogo JSON das ferramentas instaladas
agent-bench doctor NOME      # diagnóstico JSON, sem modificar a sessão
```

No desktop da bancada: F1 abre a central, Super+Enter abre o terminal, Alt+Tab alterna janelas e Alt+F4 fecha a janela atual. Esses atalhos são internos à bancada e não alteram os atalhos do compositor humano.

O catálogo detecta os executáveis disponíveis na máquina. Ferramentas gráficas devem ser lançadas por `agent-bench`; o catálogo não autoriza conectar um cliente ao navegador pessoal. `doctor` informa conexão ao display, workspace reservado, controle humano, ferramentas essenciais e endpoint CDP exclusivo. Navegador fechado é informado separadamente de falha do desktop.

## Higiene das abas e bancadas

Ao concluir uma pesquisa, descartar uma oportunidade ou salvar o comprovante de uma ação, feche as abas próprias que não têm próxima ação. Reutilize a aba de pesquisa e a aba já aberta para a mesma URL. Não deixe buscas, duplicatas ou páginas concluídas acumularem entre rodadas.

Preserve apenas trabalho em andamento, formulários com dados ainda não salvos, envios incertos a reconciliar e entregas solicitadas pelo humano. Registre o motivo e o próximo passo no checkpoint. Salve comprovantes antes de fechar; nunca repita um envio para reconstruir evidência. A limpeza considera propriedade e estado, sem cota arbitrária de abas. Não feche abas de outro agente ou do humano. Ao retomar, reconcilie o registro de abas com as que ainda existem. Ao liberar uma bancada, encerre os recursos próprios concluídos e deixe explícito o que precisa continuar.

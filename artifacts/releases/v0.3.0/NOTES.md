# omarchy-agent-bench v0.3.0

27 de setembro de 2026

Essa versão junta tudo que as bancadas ganharam desde a v0.2.0 e resolve o que mais atrapalhava no dia a dia: login barrado pelo Cloudflare, bancada que nunca fechava e MCP pesado demais pra carregar em toda sessão.

## Novidades

- **Fila pesada** · `agent-bench fila` organiza render 3D/WebGL, vídeo e captura longa: uma vaga por vez, na ordem de chegada, vencendo em 20 min sem renovação. Quem tá fora da vaga continua rodando, limitado a 1,5 núcleo, e as bancadas juntas não passam de 8 núcleos.
- **Quem tá usando cada bancada** · O servidor da bancada registra agente, projeto e lançador (Maestri, Herdr, tmux, terminal, Hermes, DailyWork) em `runtime/<nome>/origins.json`, então dá pra saber qual sessão tá por trás de cada janela.
- **Navegação por acessibilidade** · `agent-bench-web` e a ferramenta `bench_web` leem a página por snapshot de acessibilidade, com referência de elemento, preenchimento Unicode e abas marcadas por missão, sem extensão no navegador.
- **Chromium convencional opcional** · `agent-bench-native` abre um Chromium sem CDP, controlado por entrada nativa na bancada, pra sites que recusam navegador em modo de depuração.
- **Preparo de perfil** · `agent-bench-profile` cria o perfil inicial de uma bancada a partir da semente offline, em modo simulação por padrão.

## Melhorias

- **MCP mais leve** · Sem `--tools`, o `agent-bench-mcp` publica as 20 ferramentas que os agentes realmente usam (mais de 97% de 476 chamadas medidas), uns 8,4 mil tokens de schema em vez de uns 20 mil. `--tools all` devolve o catálogo completo, e sem cua-driver só as ferramentas nativas saem do ar.
- **Bancada ociosa fecha sozinha** · Depois de 25 min sem comando de agente, a bancada para, a menos que tenha cliente CDP conectado ou processo nativo gastando CPU de verdade. Aba esquecida e app Electron parado não seguram mais a bancada.
- **Barra do Omarchy informada** · O supervisor das janelas publica `bar.json` com dono, atividade e tipo de cada bancada pra barra mostrar.
- **Visita sem erro** · `visit` agora cai num workspace compartilhado em vez de falhar antes de trocar.

## Correções

- **Cloudflare deixa logar** · O Chromium sobe com porta CDP fixa por bancada e GPU real, sem `--remote-debugging-port=0` (que liga o `navigator.webdriver`) nem `--disable-gpu` (que tirava o WebGL). Nos testes de 23/09 o desafio gerenciado do Cloudflare passou com cliente CDP conectado, e as sessões logadas das bancadas seguem nesse lançamento.
- **Um Chromium por perfil** · `ensure` em paralelo abria três Chromium no mesmo perfil; agora a abertura segura uma trava exclusiva.
- **CDP liberado** · O título do processo principal do Chromium quebrava a checagem de perfil ambíguo e bloqueava o CDP em todas as bancadas.
- **Cookies salvos ao parar** · O stop fecha o Chromium antes do Xvnc, então os cookies são gravados e não aparece o aviso de restaurar sessão.
- **Reaper voltou a funcionar** · O inventário antigo marcava toda bancada como desconhecida por causa do `fusermount3` do portal de documentos, e nenhuma bancada era encerrada.
- **Instalador não apaga o hub** · `install.sh` com prefixo `~/.agents` substituía o hub de skills e o `bin/` inteiros; agora mexe só nos próprios arquivos.

## Sistemas

- **Perfis sem cópia de identidade** · Bancada nova ganha perfil vazio marcado `.efemero`, apagado ao parar. Só o `cofre` nasce persistente, e perfis que já existem são usados como estão. `profile prepare` não copia mais a semente, cujas sessões clonadas caíam todas juntas em qualquer logout.
- **cua-driver 0.29** · O controle nativo recusa `install_extension`, junto de `set_config` e `launch_app`, porque ele grava extensão no HOME compartilhado e mudaria todas as bancadas de uma vez.
- **Cotas de CPU no instalador** · `install.sh` copia os drop-ins de systemd da fila (`agent-bench@.service.d/10-cota.conf` e o teto do slice das bancadas); `uninstall.sh` remove.
- **Documentação** · Guias novos de navegador nativo, isolamento, ciclo de vida do CUA, retenção de sessão, descoberta de ferramentas e validação com Jcode.

## Atualização

Bancadas que já estão rodando mantêm o servidor antigo até reiniciar; o registro de origem e as travas novas valem a partir do próximo start de cada bancada. Os perfis persistentes existentes continuam onde estão.

## Validação

- 426 testes unitários aprovados (`python3 -m unittest discover -s desktop -p 'test_*.py'`), com 5 testes novos da fila.
- `py_compile` de todos os módulos, `bash -n` do instalador e `git diff --check` aprovados.

Comparação completa: https://github.com/LucasOl1337/omarchy-agent-bench/compare/v0.2.0...v0.3.0

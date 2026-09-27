# Browser convencional rápido na bancada

Esta rota abre o Chromium persistente sem CDP e usa a acessibilidade nativa do
Linux para ler textos e controles. Mouse, teclado e clipboard continuam
isolados na bancada. Não garante ausência de verificações dos sites.

## Quando usar

Use em uma missão que precisa de navegador convencional, especialmente para
investigar falhas de login associadas ao modo de depuração. Preserve browsers
ativos de outras missões. Não reinicie nem converta automaticamente uma sessão.

## Rota

1. Prepare o perfil da própria bancada pelo fluxo offline oficial, uma única
   vez. Nunca use perfil vazio como fallback nem sobrescreva perfil existente.
2. Execute `agent-bench-native open NOME URL`.
3. Confira `agent-bench-native status NOME` e `agent-bench status NOME`:
   PID e perfil da bancada, workspace 6–11 e controle do agente.
4. Conecte `agent-bench mcp NOME` novo, ou o MCP multiplexado atualizado.
   Descubra PID/window_id por `list_windows`, depois leia `get_window_state`.
5. Prefira texto/controles AT-SPI com `include_screenshot:false` nas releituras.
   Capture a tela ao iniciar e quando a árvore for incompleta ou ambígua.
   Clique pelos tokens da observação atual, sem adivinhar controles.
6. Para texto, grave e confira o clipboard próprio da bancada, selecione o
   campo e cole. Confira o valor na aplicação, não só o retorno da ferramenta.
   `type_text` retornou sucesso sem preencher um campo público no teste real.
   A rota comprovada foi `agent-bench exec NOME -- xdotool ...` no DISPLAY
   herdado, com foco pelas coordenadas AT-SPI observadas e Ctrl+V. Em campos
   comuns, Ctrl+A/C e leitura do clipboard conferem o valor. Nunca registre
   senhas nem copie campos secretos como evidência.

`bench_cdp` e `bench_web` exigem CDP e não são a rota deste modo. A CLI antiga
pode reportar o browser aberto sem CDP como desconectado. Isso não equivale a
logout: use o status nativo. Uma bancada/um browser ficam dedicados à missão,
pois CUA não faz imposição automática de propriedade por aba.

Ao encontrar MFA, CAPTCHA ou rejeição de login, preserve a página e registre a
etapa. Não repita credenciais indefinidamente. Feche abas próprias concluídas.
O modo mantém `password-store=basic` existente, não migra criptografia e não
promete transferir autenticação entre perfis.

Contrato técnico: `docs/browser-native.md` no repositório omarchy-agent-bench.
Evidência medida: `docs/browser-native-validation.md` no mesmo repositório.

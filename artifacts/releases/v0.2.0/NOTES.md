# omarchy-agent-bench v0.2.0

Esta versão reforça a convivência segura entre humano e agentes nas bancadas isoladas do Omarchy.

## Destaques

- `browser-status` verifica PID, cgroup, perfil e localização do Chromium antes de expor o CDP.
- `visit` e `close-here` tornam a visita humana previsível nos workspaces reservados 6–11.
- O CLI e o MCP agora informam modo de controle, proprietário, pressão de tarefas e erros de recuperação acionáveis.
- O navegador exige um perfil `Default` previamente preparado dentro da bancada solicitada.
- Não há fallback por cópia de cookies, criação de perfil vazio ou uso do Chromium da sessão humana.
- A suíte cobre localização do navegador, perfis, propriedade do viewer e transições de controle.

## Validação

- 53 testes unitários aprovados.
- Compilação de todos os módulos Python aprovada.
- `git diff --check` aprovado.

## Atualização

Use os arquivos do repositório como fonte da instalação. Em máquinas já configuradas, atualize os componentes mantendo os perfis persistentes existentes e sem reiniciar bancadas ativas.

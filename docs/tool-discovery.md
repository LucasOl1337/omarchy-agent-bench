# Descoberta de ferramentas sem iniciar bancada

`desktop/bench_catalog.py` consulta `cua-driver dump-docs --type mcp`, um comando
estático do binário local. Importar o módulo ou listar os schemas não executa
`ensure`, não inicia MCP/desktop e não escolhe `padrao`. O primeiro comando real
continua responsável por validar e preparar a bancada indicada.

```python
from bench_catalog import CatalogError, load_cua_tools

tools = load_cua_tools()  # Prazo padrão do dump: 5 segundos.
```

O helper resolve `cua-driver` no PATH e executa o caminho absoluto, sem shell ou
entrada interativa. Não aceita um JSON externo como catálogo. Não usa cache: a
consulta estática local medida levou cerca de 47 ms para 60 ferramentas na versão
0.28.1. Cada descoberta reflete o binário resolvido naquele momento.

O retorno preserva nomes, descrições e o conteúdo de `input_schema` como
`inputSchema`, inclusive formatos, valores nulos e requisitos. Apenas renomeia
as anotações disponíveis:

| Campo estático | Anotação MCP |
| --- | --- |
| `read_only` | `readOnlyHint` |
| `destructive` | `destructiveHint` |
| `idempotent` | `idempotentHint` |

Valores `false` permanecem `false`; campos ausentes permanecem ausentes. O dump
0.28.1 não fornece `outputSchema`, `risk`, `capabilities` ou `openWorldHint`, e o
helper não os inventa. Essas anotações são metadados, não autorização de operação.
A validação verifica o envelope e os tipos básicos; não substitui um validador
completo de JSON Schema. Catálogo vazio, nomes duplicados e entradas incompletas
são recusados em conjunto, sem publicar uma lista parcial.

Erros levantam `CatalogError` com `code`: `catalog_unavailable` (binário ausente
ou não executável), `catalog_timeout`, `catalog_failed` (saída não zero) ou
`catalog_invalid` (JSON/envelope inválido). Não há tentativa de conectar a driver,
usar outra bancada ou recorrer ao desktop humano. O stderr do binário não é
copiado para a resposta. Um prazo inválido é erro de programação (`ValueError`).

## Integração no hub

Por padrão, `Hub.tools()` usa `load_cua_tools()`, sem iniciar um driver em `padrao`.
O chamador filtra os nomes não operacionais com as mesmas
constantes usadas no despacho e só então chama `inject_bench_schema`. Este helper
não decide quais ferramentas têm rota autorizada; listar uma ferramenta no dump
estático não significa que ela possa ser operada pela bancada.

Em erro de descoberta, `tools/list` responde com erro JSON-RPC, preservando
o processo do hub. Não retorna uma lista parcial nem inicia CUA para compensar.
`initialize` e chamadas já autorizadas de
ferramentas próprias do hub não precisam consultar o catálogo.

## Lista explícita por tarefa

`agent-bench-mcp` sem opções mantém o catálogo completo disponível hoje. Para
uma conexão com menos schemas, o operador pode escolher nomes explicitamente:

```sh
agent-bench-mcp --tools bench_list
agent-bench-mcp --tools bench_list bench_doctor
agent-bench-mcp --tools bench_list bench_ensure bench_browser bench_web bench_cdp
```

`--tools` aceita um ou mais nomes separados por espaço. Exemplo de `args` em
uma configuração MCP: `["--tools", "bench_list"]`. Não se alteram configurações
de harness automaticamente. Não há perfis implícitos: são exemplos de listas,
e o operador escolhe os nomes adequados à tarefa. `bench_list` é leitura pura;
`bench_doctor` chama `ensure` e pode preparar/reconciliar a bancada e seu viewer.

`bench_list` devolve uma lista JSON com `name`, `display`, `geometry`,
`control_mode` e `owner`, mais o contexto de diagnóstico abaixo. Consulta somente
os sockets de status existentes; não inicia bancadas, viewers ou CUA, não assume
controle e não atualiza metadados/atividade. Sockets de bancada indisponíveis ou
com resposta inválida são omitidos: a lista não é um inventário de sessões salvas
nem uma garantia de que todas as unidades systemd foram encontradas.

| Campo | Significado |
|---|---|
| `workspace` | Atribuição inteira informada pelo supervisor, ou `null` se ausente, inválida ou indisponível. A consulta usa prazo de 1 s; sua falha não remove a bancada da lista. Não comprova viewer vivo, foco ou permissão de operar fora de 6–11. |
| `metadata_version` | `2` se esse valor já estiver registrado como inteiro; caso contrário, `null`. A leitura não migra registros legados. |
| `owner_origin` | `legacy_label` ou `first_observed_actor`, quando registrado; caso contrário, `null`. |
| `last_actor` | Último rótulo não vazio de solicitante registrado, ou `null`. Não identifica um executor exclusivo nem comprova que ele continua ativo. |
| `last_seen_at` | Timestamp numérico finito e não negativo do pedido registrado, ou `null`. Não é heartbeat nem horário comprovado da última entrada gráfica. |

O `owner` histórico permanece intacto, inclusive `unknown`; não é substituído
pelo cliente que lista nem por `last_actor`. Os campos novos ausentes ou de tipo
inválido ficam `null`, sem reparar arquivos. O registro persistente v2 continua
prioritário sobre o espelho runtime. Esses rótulos são informativos; consulte
[metadados de atores](task-ownership.md) para os limites de ownership.

Uma seleção somente de ferramentas próprias do hub, como `bench_list` ou
`bench_web`, não consulta o dump estático nem exige `cua-driver` instalado para
inicializar/publicar seu catálogo. As operações escolhidas ainda exigem seus
recursos normais. Uma seleção com ferramentas nativas consulta o dump estático
na validação inicial e a cada `tools/list`, preservando a descoberta sem cache.
O modo completo mantém seu comportamento anterior de descoberta.

Lista vazia, nome vazio/desconhecido ou ferramenta já indisponível são erros de
configuração, com saída 2; a validação de nomes usa `TOOLS_CONFIG_INVALID`.
Não há fallback para a lista
completa nem publicação parcial. Nomes repetidos são deduplicados; a ordem de
publicação continua sendo a ordem original do catálogo, não a ordem dos argumentos.
Se o catálogo nativo mudar e um nome selecionado desaparecer, a próxima
descoberta retorna erro em vez de omiti-lo silenciosamente.

`tools/list` publica somente a seleção, com os mesmos contratos. Uma chamada
fora dela recebe `TOOL_NOT_ALLOWED` antes de `handle_hub`, `ensure`, gate ou
acesso/criação de driver, inclusive sem descoberta prévia ou com schema antigo.
A conexão continua apta a receber a próxima chamada permitida. A seleção é
fixa por conexão; alterá-la exige iniciar outra conexão explicitamente.

As recusas existentes, os guardas, arguments ausente/null e o default foreground
permanecem. A lista não reabilita ferramentas recusadas nem é uma sandbox contra
outras APIs/CLI: uma ferramenta ampla, como `bench_exec`, conserva sua capacidade
normal se selecionada. O MCP dedicado `agent-bench mcp NOME`, a CLI e clientes
vivos não são alterados por essa opção. Sem `--tools`, todas as capacidades
atualmente publicadas continuam disponíveis.

## Validação

```sh
cd desktop
python3 -m unittest test_bench_catalog -v
```

Os testes usam executáveis falsos: validam o comando permitido, stdin fechado,
ausência de fallback, timeout com processo encerrado, troca de binário sem cache
obsoleto, erros explícitos e preservação dos schemas/anotações. A comparação real
usa somente o dump estático; não depende de criar uma bancada para listar schemas.

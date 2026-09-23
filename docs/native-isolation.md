# Alvos nativos por PID

O DISPLAY e o D-Bus da bancada não restringem operações globais por PID. O
`cua-driver 0.28.1` descreve `kill_app(pid)` como encerramento forçado por PID e
`list_apps` como enumeração de `/proc`. Por isso os dois transports MCP aplicam
uma barreira comum em `bench_native.py` antes de encaminhar alvos explícitos.

`pid` e `target.pid` devem ser inteiros positivos e corresponder ao mesmo alvo
quando ambos aparecem. Zero, negativos, booleanos, strings, valores conflitantes
ou processos de origem incerta são recusados. Isso vale também para leituras
como get_window_state: um pedido de leitura não recebe acesso a um PID externo.

A validação consulta o status da bancada e ancora sua raiz no Xvnc real:

1. O status precisa nomear a bancada solicitada.
2. O PID do servidor precisa ser Xvnc, do usuário local, no cgroup v2 esperado e
   listado em cgroup.procs.
3. O alvo precisa pertencer à mesma raiz ou subgrupo. Encontrar o mesmo nome de
   unidade em outra árvore não basta.
4. Um auxiliar CUA em cgroup separado só é aceito quando systemd confirma Id,
   LoadState, ActiveState, ControlGroup exato e BindsTo para essa bancada. Não é
   aceito apenas porque seu nome contém `agent-bench-cua`.
5. PID/starttime/cgroup são conferidos novamente durante a validação. Ausência,
   mudança de processo, erro de leitura ou de consulta preservam a recusa.

A identidade original é mantida durante a inicialização do driver ou a espera
na trava. Imediatamente antes do envio, dentro da trava de I/O/controle, os PIDs
e as propriedades da unidade auxiliar são consultados novamente, sem aproveitar
cache de BindsTo/ControlGroup. Uma recusa nessa fase significa que a ação não foi
enviada, não um resultado de mutação incerto. O bridge libera seu gate ao recusar.

As consultas ao gerenciador systemd são apenas `show`, com o ambiente de controle
validado. A barreira não envia sinais e não encerra processos. Pedidos válidos
continuam sujeitos aos gates de controle humano já existentes.

## Modo de entrega padrão

Nos dois MCPs da bancada, o adaptador envia `delivery_mode: "foreground"` quando
esse campo é omitido em `click`, `double_click`, `right_click`, `drag`, `type_text`,
`press_key`, `hotkey` e `scroll`. A bancada tem DISPLAY e foco próprios; o padrão
upstream `background` pode tentar criar dispositivo uinput, operação recusada
pela proteção processual. `foreground` é o padrão adequado para esse ambiente,
continuando sob os mesmos gates de controle humano e guardas de alvo/ambiente.
Isso não constitui garantia de execução ou de resultado na aplicação.

Uma normalização pura compartilhada pelos dois transports copia somente os
objetos necessários para acrescentar o campo. Qualquer valor explícito é
preservado, inclusive `background`, `null` ou valor inválido; cabe ao driver
validá-lo. `arguments` ausente ou não objeto, inclusive `null`, não recebe a
normalização; o adaptador não cria argumentos para tornar válido um payload
inválido. Não há fallback automático, tradução de argumentos ou repetição de
ação. Outras ferramentas e mensagens não recebem esse default; navegador e
replay continuam recusados pelos guardas existentes.

Os dois catálogos publicam `default: "foreground"` e a descrição correspondente
somente no campo `delivery_mode` desses oito comandos, preservando tipo, enum e
demais campos. O catálogo recebido do driver não é alterado. Testes fake
conferem encaminhamento, valores explícitos, ausência de mutação dos pedidos e
catálogos de origem, e preservação das outras rotas; não operam a interface.

## Descoberta

`list_apps` e `list_windows` devolvem somente alvos cujo PID foi validado.
Aplicativos instalados mas não executados (`pid=0`, `running=false`) podem
continuar no catálogo; PID zero continua inválido como alvo de operação.
Janelas sem PID confirmado e entradas de outra bancada ou do desktop humano não
são oferecidas como alvos. Isso pode omitir janelas sem metadados de processo;
não é uma afirmação de que elas não existem.

O filtro reconstrói `structuredContent` e o texto do MCP a partir do resultado
filtrado. Filtrar só a representação estruturada deixaria os mesmos PIDs e
títulos no texto original. Campos globais/formatos que o adaptador não conhece
não são repassados como inventário vazio ou resultado bruto: a chamada informa
`NATIVE_DISCOVERY_UNCERTAIN`.

`get_accessibility_tree` do driver instalado não tem outputSchema e sua descrição
inclui processos globais. Essa descoberta legada retorna
`NATIVE_DISCOVERY_UNSUPPORTED` **antes de consultar o driver**. Use list_windows
para descobrir janelas da bancada e get_window_state com o PID/janela exatos para
obter AT-SPI, texto e imagem. Essa é uma mudança de compatibilidade explícita;
um adaptador para a descoberta legada depende de evidência do formato real.

## Navegação e replay sem vínculo

`page` aceita uma porta CDP explícita e não vincula janela e aba. `browser_prepare`
pode lançar/selecionar um perfil, e `get_browser_state`/as ferramentas `browser_*`
mantêm alvos e sessões internos que não foram vinculados à bancada e à missão.
Um PID próprio no pedido não comprova o destino de CDP, perfil, sessão ou aba.
Por isso as seguintes ferramentas retornam `NATIVE_BROWSER_UNBOUND` antes do
envio, com orientação para `bench_web` ou `bench_cdp` com superfície validada:

- `page`, `get_browser_state`, `browser_prepare`, `browser_navigate`;
- `browser_click`, `browser_type`, `browser_dialog`, `browser_set_input_files`;
- `browser_download`, `browser_pointer`.

Os alvos e as sessões dessas APIs do CUA não compartilham automaticamente o
registro de missão do `bench_web`. Reativá-las requer comprovar essa vinculação.

`replay_trajectory` executa ações de arquivo no dispatcher interno do driver,
contornando a validação individual de PID e de ferramentas. É recusado com
`NATIVE_REPLAY_UNBOUND`: observe o estado atual e execute ações individuais sob
o gate da bancada. A recusa não abre nem reinterpreta o arquivo de trajetória.

`NATIVE_UNSUPPORTED_TOOLS` e `available_tools()` tornam explícito o conjunto
indisponível. O bridge dedicado filtra o `tools/list` correlacionado sem consultar
processos; o hub deve usar o mesmo filtro ao publicar seu catálogo. Os guardas de
despacho permanecem necessários para clientes que guardaram schemas antigos.
Estas recusas são mudanças de compatibilidade deliberadas, até existir um
adaptador com vínculo comprovado. Elas não desabilitam os inputs nativos da
bancada, `list_windows` ou `get_window_state` com alvo validado.

## Aplicativos duradouros

`launch_app` é omitido dos dois catálogos MCP e recusado com
`NATIVE_LAUNCH_TRANSIENT` antes do encaminhamento, inclusive para clientes com
schema antigo. Um aplicativo novo criado pelo driver herda sua unidade temporária;
o cleanup CUA usa `KillMode=control-group` e pode encerrá-lo junto da conexão.
A recusa preserva esse cleanup e não converte a solicitação em outro lançamento.

Para aplicativos nativos, usar `bench_launch` com `argv` explícito no hub, ou
`agent-bench launch NOME -- APP ARG...` no terminal. Essas rotas encaminham ao
servidor da bancada; a vida do aplicativo deixa de depender da conexão CUA.
Encerrar a bancada ainda encerra seus processos. Depois, descobrir e validar
o PID/janela antes de usar as ferramentas nativas.

Para navegador, usar `bench_browser` ou `agent-bench browser NOME`, com o perfil
persistente preparado e validado. Lançar browser por argv, nome, entrada desktop
ou xdg-open não substitui essa validação de perfil e instância.

A implementação Linux do CUA 0.28.1 usa spawn direto e pode resolver entradas
desktop ou xdg-open; a descrição de `launch_path` como shell não corresponde ao
helper. Não reinterpretamos strings, quoting ou nomes como argv nem duplicamos
a resolução de aplicativos. [Fonte da versão](https://github.com/trycua/cua/blob/cua-driver-rs-v0.28.1/libs/cua-driver/rust/crates/platform-linux/src/tools/impl_.rs#L1176-L1229).

Os testes fake verificam zero encaminhamento de `launch_app` nos dois MCPs,
omissão nos catálogos e preservação do argv de `bench_launch` enviado ao servidor.
Nenhum aplicativo real é aberto pelos testes.

## Configuração persistente do driver

`set_config` é omitido dos dois catálogos MCP e recusado com
`NATIVE_CONFIG_GLOBAL` antes do envio, inclusive para clientes com schema antigo.
No Linux, CUA 0.28.1 persiste as quatro chaves `capture_mode`,
`max_image_dimension`, `experimental_pip` e `experimental_pip_geometry`, tanto
no formato `{key, value}` quanto por campos diretos. As duas primeiras também
mudam a memória imediatamente; isso não as torna efêmeras. O catálogo menciona
a persistência de PiP, mas a implementação escreve todas as quatro.

O helper usa `HOME/.cua-driver/config.json`, com fallback USERPROFILE, sem
override independente do HOME/XDG. `launch_cua` preserva HOME; logo essa escrita
alcançaria a configuração compartilhada do usuário. Esta barreira não altera
HOME, configurações pessoais ou os outros controles nativos. Fontes oficiais da
versão: [SetConfigTool Linux](https://github.com/trycua/cua/blob/cua-driver-rs-v0.28.1/libs/cua-driver/rust/crates/platform-linux/src/tools/impl_.rs#L8332-L8487)
e [resolução/escrita do caminho](https://github.com/trycua/cua/blob/cua-driver-rs-v0.28.1/libs/cua-driver/rust/crates/pip-preview/src/lib.rs#L18-L56).

`get_config` permanece disponível como leitura. Para uma imagem menor, usar
`get_window_state(..., max_dimension=800)` por chamada. Esse parâmetro limita a
imagem sem persistir configuração; o menor limite entre ele e o teto configurado
vence. Não promete elevar o teto existente nem substituir integralmente
`max_image_dimension`. Não existe chave efêmera autorizada em `set_config`.

Testes com driver fake cobrem ambas as formas de entrada, payload misto,
parâmetros vazios/desconhecidos, ausência de encaminhamento nos dois MCPs e
preservação de `get_config` e do limite por chamada. Nenhum teste lê ou altera
o config real, inicia gravação ou opera a interface.

## Cobertura e limites

O hub verifica antes de criar/consultar seu driver para a chamada; o bridge
dedicado verifica antes de encaminhar cada mensagem. Ambos usam o mesmo módulo
para filtrar descobertas. O bridge dedicado processa chamadas sequencialmente e
correlaciona cada resposta com o único pedido em andamento, sem trocar filtros.
IDs podem ser reutilizados depois de uma resposta. Prazos e limites de
notifications estão em `docs/cua-lifecycle.md`.

Esta é uma proteção de roteamento, não uma sandbox contra programas do mesmo UID.
Ela não impede chamadas diretas a APIs do SO por fora desses transports nem
transforma uma identificação de PID em prova de autorização para apagar trabalho.
Depois do último check ainda existe uma janela entre validar e o driver agir;
o backend recebe PID numérico, não pidfd. Não se promete atomicidade contra toda
corrida de saída/reutilização de PID. Também não é uma auditoria completa de
outras capacidades globais do driver.

Os testes usam árvores temporárias de `/proc`/cgroup, propriedades systemd
simuladas e driver fake que apenas registra JSON. Cobrem também troca de PID na
criação/espera do driver, liberação de gate após recusa, omissão de ferramentas no
catálogo e zero encaminhamento de navegador/replay mesmo com PID próprio. Não há chamada real de kill_app
nem tentativa de enviar sinal a PID humano, vizinho ou da bancada. A prova ao
vivo deve continuar usando somente leitura/ações próprias não destrutivas.

```sh
python -m unittest discover -s desktop -p 'test_bench_native.py' -v
```

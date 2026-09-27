# Retenção de trabalho nativo

O encerramento automático por ociosidade só acontece quando as verificações
confirmam uma sessão descartável. Tempo sem comandos não prova ausência de
trabalho: um editor, terminal ou processo sem janela pode continuar produzindo
ou mantendo dados em memória.

`should_reap(name)` preserva a bancada quando:

- É a bancada `padrao`, o humano assumiu ou há operação protegida pela trava.
- A atividade é recente ou seu horário não está disponível.
- O Chromium próprio tem páginas de trabalho ou seu estado está incerto.
- Existe aplicativo/processo nativo além da infraestrutura conhecida.
- O inventário de processos não pôde ser confirmado.

A verificação nativa usa somente o status da bancada e leituras de `/proc` e
cgroup v2. Confere que o PID informado é Xvnc na unidade exata
`agent-bench@NOME.service` e inventaria essa árvore e seus subgrupos. Não consulta
o display humano, não depende de uma janela estar visível e não renova o relógio
de atividade apenas por observar a sessão.

São considerados infraestrutura o Xvnc validado, Openbox com a configuração
própria, controlador e central com seus caminhos/argumentos esperados, além de
D-Bus da sessão. A árvore do Chromium é aceita apenas para o processo do perfil
próprio e descendentes do mesmo executável; um visualizador ou editor lançado
pelo navegador continua sendo trabalho nativo. A checagem de páginas Chromium
acontece separadamente antes da decisão de encerrar.

A identidade do executável vem de `/proc/PID/exe`, comparando device/inode entre
o Chromium próprio e seus descendentes. `argv[0]` pode ser reescrito pelo Chromium
e não identifica o binário. Outro executável descendente, mesmo chamado chromium,
continua sendo trabalho nativo. O diagnóstico usa apenas o basename do alvo de
`exe`, nunca `argv[0]` ou os argumentos. Os argumentos ainda são consultados
internamente para reconhecer a infraestrutura já listada, sem expô-los.

Qualquer outro processo preserva a bancada. Isso inclui editores e terminais
minimizados, jobs sem janela, auxiliares ainda não reconhecidos e comandos cujo
papel não está claro. Erros de acesso, PID desaparecido, origem estrangeira ou
inventário que muda durante a leitura retornam estado `unknown`, que também
preserva. Os diagnósticos retornam PID e nome do executável; não expõem argumentos
que possam conter dados da tarefa.
O inventário também reconfere starttime, executável, parentesco, argumentos e
pertencimento à bancada antes de concluir; divergência, `exe` ausente/inacessível
ou executável removido resulta em `unknown`. Essa correção não reconhece novos
serviços como infraestrutura.

`.keep` continua significando retenção do perfil em disco contra o GC. Não
impede, por si só, o encerramento de uma sessão comprovadamente ociosa. Para
encerrar trabalho terminado, o proprietário continua usando `agent-bench stop`.

## Limites

Nenhuma heurística prova que o estado de um aplicativo foi salvo. Esta regra
prefere manter sessões incertas e pode reter uma bancada por causa de um auxiliar
inofensivo. Não existe detecção automática de buffers modificados nem dedução de
que um terminal parado esteja livre de trabalho. A central padrão é considerada
infraestrutura descartável; seus controles de teste não viram documentos com
persistência garantida.

Auxiliares AT-SPI (`at-spi-bus-launcher`, `at-spi2-registryd` e seu D-Bus) e
crash handlers independentes ainda não têm identidade/argumentos validados pela
lista. Sua presença conserva a sessão. Ampliar essa lista exige uma evidência
própria de caminho, argumentos e pertencimento; nomes de executável isolados não
são suficientes para descartar processos.

A inspeção é uma fotografia. Há uma janela entre a leitura e o stop; esta mudança
não introduz lease, exclusividade nova ou transação com o lançamento de apps.
Trabalho movido para fora do cgroup da bancada também fica fora do inventário.
MCPs com código antigo e processos de reaper já carregados precisam adotar a
versão atual antes de receber esse comportamento.

## Verificação sem GUI

```sh
python -m unittest discover -s desktop -p 'test_bench_ops.py' -v
python -m unittest discover -s desktop -p 'test_*.py'
```

As fixtures criam árvores temporárias de `/proc` e cgroups; não iniciam aplicativos
nem encerram serviços. Cobrem central vazia, Chromium próprio e vazio, editor,
terminal, job sem janela, processo em subgrupo, aplicativo filho do Chromium,
vizinho não consultado, erro e mudança de inventário, CDP incerto e a distinção
entre idle-stop e `.keep`. Também cobrem `argv[0]` reescrito, ausência de argumentos
no diagnóstico, mesmo inode com outro nome, executável diferente com nome igual,
troca de PID/binário e manutenção de auxiliares AT-SPI/portal/gvfs/crashpad como busy.

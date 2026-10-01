# Encerramento por inatividade

O supervisor `agent-bench-views.service` verifica as bancadas a cada 60 s.
O prazo padrão é **15 minutos sem comandos de agente**, inclusive nas bancadas
com login. `AGENT_BENCH_IDLE_SECONDS` pode definir outro prazo; `bar.json`
publica o valor efetivo em `idle_timeout_seconds`, lido pelo DailyWork.
Abrir o painel, ler status ou observar a tela não renova a atividade.

`should_reap(name)` preserva a bancada quando:

- O humano está no controle ou há operação protegida pela trava de entrada.
- A atividade é recente ou o horário não está disponível.
- Há um cliente conectado ao CDP do Chromium da bancada.
- Há vaga pesada válida ou espera renovada na fila de trabalho pesado.
- Há processo nativo além da infraestrutura e do navegador.
- O inventário de processos ou a fila de trabalho pesado está indisponível.
- As amostras de CPU ainda indicam trabalho nativo em andamento.

Um editor, terminal ou job esperando rede continua protegido mesmo com CPU
baixa. A inspeção de processos usa o status da própria bancada, `/proc` e cgroup
v2. O PID informado precisa pertencer à unidade `agent-bench@NOME.service`.
`BENCH_INFRA` lista o navegador e auxiliares de desktop; o controlador e a
central da bancada são reconhecidos pelo comando. Outros processos mantêm a
bancada aberta. Sem processo nativo, não é necessário aguardar uma janela de
amostras de CPU depois que o supervisor inicia.

Abas largadas e interfaces Electron sem operação ativa não reservam bancada.
Render WebGL, vídeo e captura longa usam a fila pesada; sua vaga válida impede
que uma operação em curso seja confundida com uma aba esquecida. A vaga segue
seu TTL próprio e precisa ser renovada conforme o guia.

O reaper segura `input.lock` exclusivamente desde a checagem final até o stop.
Um novo comando protegido do agente não começa nesse intervalo. O controle
humano, CDP e a atividade são reconferidos sob essa trava.

## Perfis e trabalho salvo

O encerramento automático marca `preserve-profile-on-stop` no runtime e chama
o stop existente. O Chromium recebe SIGTERM antes do Xvnc pra gravar cookies.
Todos os perfis permanecem em disco, inclusive os marcados como efêmeros;
checkpoints, arquivos salvos e `.keep` também permanecem. A marca temporária é
removida ao terminar a parada.

`.keep` protege o perfil contra GC de disco, mas não mantém uma bancada ociosa
aberta. O GC continua separado, com prévia e aplicação explícita. Uma parada
manual conserva seu comportamento anterior de apagar perfil efêmero, enquanto
o perfil persistente fica guardado. Não houve alteração do login ou cópia de
cookies entre bancadas.

## Limites e verificação

Nenhuma heurística comprova que um documento foi salvo. Jobs fora do cgroup,
clientes antigos sem trava e conteúdo pendente em uma interface Electron não
são totalmente detectáveis. Trabalho que precisa permanecer em curso usa as
rotas protegidas e, quando pesado, a fila com vaga renovada.

O supervisor já iniciado precisa carregar o código atualizado. Reiniciar
somente `agent-bench-views.service` preserva os desktops e as atribuições de
workspace, mas recria seus viewers; não reinicie bancadas de outras tarefas.

As verificações existentes em `test_bench_ops.py` cobrem o limite de 900 s,
CDP e atividade recente, jobs com CPU zero, fila válida e expirada, inventário
incerto, trava mantida durante o stop e retenção de perfil/checkpoint. As
fixtures são temporárias, sem parar serviços reais nem consultar tela humana.

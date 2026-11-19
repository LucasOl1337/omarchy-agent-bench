# Reutilizar o pareamento existente do WhatsApp Web

Quando uma missão autorizada precisa do WhatsApp Web, ela usa uma sessão já pareada da conta necessária onde estiver acessível aos agentes, inclusive em outra bancada ou máquina. O pareamento fica no perfil que já o possui, o que evita repetir o vínculo a cada missão ou copiar cookies entre navegadores.

- A conta indicada na missão tem prioridade. Se a missão omitir a conta, vale a conta associada ao projeto. Se restar ambiguidade entre contas, o agente pergunta antes de abrir a conversa.
- Se nenhuma sessão da conta estiver acessível, o agente prepara o vínculo em um perfil persistente. A rota padrão é o pareamento por número de telefone e código, usando o controle autorizado do POCO. O agente chama o Lucas apenas se o fluxo depender de uma ação que ele precisa fazer no aparelho, como desbloquear ou confirmar por biometria.

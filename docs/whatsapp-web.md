# WhatsApp Web nas bancadas

Cadastre as bancadas que podem ter uma sessão da conta em `~/.config/agent-bench/whatsapp.json`. Use apelidos de conta e nomes de máquinas acessíveis via SSH. A ordem das bancadas define qual sessão será tentada primeiro.

```json
{
  "accounts": {
    "trabalho": [{"host": "lol", "bench": "cofre"}]
  },
  "projects": {}
}
```

No próprio PC, use `"host": "local"`. Cada projeto pode ter uma conta padrão em `projects`. Quando houver apenas uma conta cadastrada e a missão omitir conta e projeto, a CLI usa essa conta.

```sh
agent-bench whatsapp nome-da-missao --account trabalho
```

O retorno `paired` informa a máquina, a bancada e a aba da missão. Use `agent-bench-web` nessa máquina para operar a aba com os mesmos nomes de bancada e missão. Se a máquina for remota, rode os comandos pela conexão SSH usual. Ao terminar, execute `finish` na máquina que hospeda a aba. O comando detecta uma aba em uso por outra missão e preserva o controle dessa missão.

Se o resultado for `pairing_required`, prepare o vínculo em um perfil persistente da conta. No WhatsApp Web, escolha o vínculo por número de telefone e insira o código pelo controle autorizado do POCO. Se houver biometria ou desbloqueio obrigatório, peça ao Lucas a confirmação no aparelho. `unavailable` indica que pelo menos uma rota ficou ocupada, inacessível ou sem resultado conclusivo. Verifique essas rotas antes de abrir um novo pareamento.

# Stark Bank Challenge

Integração que emite Invoices periodicamente no Sandbox do Stark Bank e, ao receber o
callback de crédito, repassa o valor líquido via Transfer.

## Decisões de projeto

### 1. Qual taxa é descontada do valor transferido

O enunciado pede o valor recebido *"minus eventual fees"*. Isso é a taxa do **Invoice**
(`invoice.fee`); a Transfer tem custo próprio, que sai por fora.

**Decisão:** transferir `invoice.amount - invoice.fee`, aceitando o custo da Transfer como
despesa da operação. Descontar uma estimativa da taxa de transferência faria transferir
menos do que entrou, deixando dinheiro parado na conta sem justificativa rastreável.

No sandbox a taxa **é zero** (verificado no evento de crédito e na transação gerada), mas
a subtração é feita mesmo assim: `net_amount(amount, fee)` é sempre chamado, com o `fee`
lido do evento. Em produção a taxa não é zero, e um atalho aqui esconderia o erro.

### 2. Uma Transfer por Invoice, ou Transfers agregadas

**Decisão:** 1:1 — uma Transfer para cada Invoice creditado.

O id do Invoice vira o `external_id` da Transfer, e essa derivação determinística é o que
impede pagamento em dobro. A agregação economizaria taxas, mas exigiria controle de janela
e tornaria a conciliação difícil de provar — com 1:1, `SUM(transfers) == SUM(credited)`
fecha linha a linha.

### 3. Postgres como sistema de registro

O Postgres não deixa gravar duas linhas com o mesmo valor numa coluna marcada como
`UNIQUE`. O projeto usa isso em dois lugares:

- `invoices.stark_invoice_id` — o mesmo invoice nunca é gravado duas vezes.
- `transfers.external_id` — derivado do id do invoice, impede duas Transfers para o
  mesmo crédito.

**A ordem das operações é o que importa.** O caminho natural seria consultar antes de
agir, mas ele falha:

```python
if existe(external_id):
    return
stark.create_transfer(...)      # move o dinheiro
transfers.insert(external_id)   # registra
```

Entre consultar e registrar existe um intervalo em que a linha ainda não está gravada.
Dois workers que passem pela consulta nesse intervalo veem "não existe" e ambos seguem
para mover o dinheiro:

```
Worker A: consulta → não existe ──► transfere 💸 ──► grava
Worker B:    consulta → não existe ──► transfere 💸 ──► gravação recusada
```

O `UNIQUE` recusa a segunda gravação, mas tarde: o dinheiro já saiu duas vezes.

**A solução é gravar primeiro.** Uma única instrução tenta gravar a linha e devolve o que
conseguiu:

```python
# grava se ainda não existir; devolve a linha se gravou, ou vazio se já existia
if not transfers.insert_pending(external_id, invoice_id, net):
    return                       # outro worker chegou antes — desiste

transfer = stark.create_transfer(amount=net, external_id=external_id)
transfers.mark_created(external_id, transfer.id)
```

Como é uma operação só, não há intervalo para outro worker se encaixar: o Postgres
enfileira as tentativas simultâneas, a primeira grava e as demais recebem vazio — sem
erro, é o resultado esperado. Quem recebeu vazio desiste **antes** de chamar o Stark.

Verificado com 8 workers simultâneos na mesma invoice: a versão que consulta antes chamou
o Stark 3 vezes; esta, uma.

### 4. Redpanda como fila entre webhook e worker

**Por que uma fila.** Para que a Transfer saia o mais perto possível de tempo real. O
worker fica parado esperando o aviso da fila e recebe em milissegundos. A alternativa
seria ele consultar o banco de tempos em tempos perguntando "chegou algo?" — e aí a
espera média seria metade do intervalo escolhido, além de fazer consultas mesmo quando
não há nada a fazer.

**Por que Redpanda.** Ele funciona igual ao Kafka: mesmo comportamento, mesma biblioteca
cliente, nenhuma linha de código diferente. A diferença é o peso — o Kafka ocupava 345 MB
de memória e consumia CPU mesmo parado, porque roda sobre a JVM. O Redpanda não.

**Uma mensagem pode chegar duas vezes.** É uma garantia da fila: ela prefere entregar de
novo a arriscar não entregar. Por isso o worker só avisa que terminou depois que a
Transfer foi criada e gravada — se ele morrer antes disso, a mensagem volta. E se voltar,
quem impede a segunda transferência é o `external_id` da decisão 3, não a fila.

### 5. Vencimento e expiração das Invoices


**Decisão:** `due` de 1 hora e `expiration` de 6 horas. Como `expiration` conta a partir
do `due`, a janela total é de **7 horas**.

O ciclo curto faz cada invoice se resolver muito antes do fim das 24h, e o estado final do
banco fica conclusivo. Vencimento em 1 hora também provoca multa e juros nas pagas em
atraso, exercitando o caminho em que `credited_amount != nominal_amount`. O sandbox paga
em ~8 minutos, então a janela é folgada.

### 6. Os eventos chegam fora de ordem

Medido no sandbox: em 4 de 11 invoices, o `log.type = credited` chegou **antes** do
`log.type = paid`. Manter o estado numa única coluna sobrescrita a cada evento faria o
`paid` atrasado apagar o registro do crédito.

**Decisão:** o `credited` é a autoridade — chegou, a invoice está creditada e a
transferência é iniciada, independentemente do que venha depois.

Na prática, os dois fatos moram em colunas diferentes:

| Fato | Coluna | Quem escreve |
|---|---|---|
| estado do invoice no Stark | `status` | `paid`, `overdue`, `expired`, `canceled` |
| o dinheiro entrou | `credited_at` | só o evento `credited` |

Assim o `paid` que chega depois escreve `status = paid` — o mesmo valor que o próprio
crédito já gravou — e não toca no `credited_at`. Qualquer ordem de chegada converge para
o mesmo estado final, e o Fallback consulta `credited_at IS NOT NULL`, nunca `status`.

Consequência: `InvoiceStatus` **não tem `credited`**, porque isso é tipo de log e não
status — no evento de crédito o próprio Stark reporta `invoice.status = 'paid'`.

## Dev

Todos os comandos rodam **da raiz do projeto** e usam `-m`. Executar o arquivo direto
(`python app/entrypoints/issuer.py`) coloca `app/entrypoints/` no `sys.path` em vez da
raiz, e o `import app` falha.

### Preparar o ambiente

```bash
poetry install                               # dependências no .venv do projeto
docker compose up -d                         # Postgres + Redpanda
.venv/bin/python -m scripts.create_tables    # cria as tabelas; idempotente
```

O `.env` precisa de `STARK_PROJECT_ID`, `STARK_PRIVATE_KEY_PATH`,
`STARK_DESTINATION_ACCOUNT_PATH` e `DATABASE_URL`. Para gerar um novo par de chaves:

```bash
.venv/bin/python -m scripts.generate_keys    # cole a pública no console do Stark
```

### Entrypoints

```bash
# Invoice Generator — emite um lote de 8 a 12 invoices e termina
.venv/bin/python -m app.entrypoints.issuer

# Webhook — servidor HTTP, fica no ar
.venv/bin/python -m uvicorn app.entrypoints.api:app --port 8000 --reload

# Worker Transfer — consome a fila e cria as Transfers, fica no ar
.venv/bin/python -m app.entrypoints.worker

# Fallback — reconciliação, roda e termina  (ainda não implementado)
.venv/bin/python -m app.entrypoints.reconciler
```

### Expor o webhook

O Stark exige HTTPS e não alcança `localhost`. Em outro terminal:

```bash
ngrok http 8000
```

Cadastre no console do Stark a URL **com o path**, e subscription `invoice`:

```
https://<subdominio>.ngrok-free.app/webhooks/stark
```

Teste antes de configurar — **400 é o resultado esperado**, porque significa que a
requisição chegou ao código e a assinatura foi corretamente rejeitada:

```bash
curl -X POST https://<subdominio>.ngrok-free.app/webhooks/stark -d '{}'
```

O inspetor em `http://localhost:4040` mostra cada entrega recebida, com body e headers.

### Depurar

O [.vscode/launch.json](.vscode/launch.json) tem uma configuração por entrypoint. F5 e
escolher na lista; os breakpoints funcionam normalmente.

### Recriar o banco do zero

Não há migrations: `create_all` só cria o que falta e nunca altera o que existe. Mudou um
enum ou uma coluna, recrie:

```bash
docker compose down -v && docker compose up -d
.venv/bin/python -m scripts.create_tables
```

## Documentos do projeto

- [arquiteture.md](arquiteture.md) — componentes e fluxo
- [testes.md](testes.md) — testes unitários obrigatórios
- [todos.md](todos.md) — checklist de implementação
- [knowledge.md](knowledge.md) — aprendizados

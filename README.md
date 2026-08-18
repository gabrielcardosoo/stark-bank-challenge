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

O id do Invoice vira o `external_id` da Transfer — no formato `transfer-{invoiceId}` —, e
essa derivação determinística é o que impede pagamento em dobro.

O prefixo importa: **`invoice-{id}` não funciona.** O Stark já usa essa string
internamente para o lançamento do crédito, e uma Transfer com o mesmo `external_id` é
recusada com *"Duplicated transfer"*. Isolado em teste — `invoice-{id}` falha, e
`transfer-{id}` com o mesmo valor e destino tem sucesso.

A agregação economizaria taxas, mas exigiria controle de janela e tornaria a conciliação
difícil de provar — com 1:1, `SUM(transfers) == SUM(credited)` fecha linha a linha.

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
banco fica conclusivo. O sandbox paga em ~8 minutos, então a janela é folgada.

Uma consequência esperada não se confirmou: invoices vencidas deveriam acumular multa e
juros ao serem pagas, mas o sandbox **não paga invoice vencida** — depois de `overdue` ela
segue direto para `expired`. Nenhuma das 53 teve `fine_amount` ou `interest_amount`
(ver Limitações).

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

### 7. O webhook assina `invoice` **e** `transfer`

Aceita pelo Stark não é dinheiro entregue. Uma Transfer nasce `created` e só depois vira
`success` — ou `failed`. Aconteceu de verdade durante os testes:

```
23:05  Transfer requested
23:05  Failed transfers: Duplicated transfer
```

Com a assinatura só de `invoice`, o banco registraria `created` e nunca saberia do
desfecho. A conciliação `SUM(transfers) == SUM(credited)` fecharia **falsamente**, contando
como transferido um valor que nunca saiu.

**Decisão:** assinar `transfer` também, e refletir o desfecho na coluna `status`.

| `log.type` | `transfers.status` | Significa |
|---|---|---|
| `created` | `created` | o Stark aceitou |
| `sending` / `processing` | `processing` | está sendo enviada |
| `success` | `success` | **dinheiro entregue** |
| `failed` | `failed` | recusada; o dinheiro não saiu |
| `canceled` | `canceled` | cancelada |

Só `success` prova entrega. Por isso `TransferStatus` deixou de ter três estados e passou
a espelhar o ciclo real.

A busca por `external_id` ignora Transfers `failed` e `canceled`: o dinheiro não saiu, e
considerá-las existentes faria o sistema registrar como entregue algo que foi recusado.

**Uma Transfer `failed` não é repetida automaticamente.** Repetir exigiria saber por que
foi recusada — no caso acima, uma nova tentativa com o mesmo `external_id` seria recusada
igual. Ela fica registrada, aparece como diferença na conciliação e é sinalizada em nível
`ERROR` no log, para revisão.

O tratamento do evento **nunca cria nem repete transferência** — só atualiza status. A
regra de que um único caminho gasta dinheiro continua valendo.

## Limitações conhecidas

O que está fora do escopo ou não pôde ser verificado, com o que foi observado no sandbox.

### Invoice paga em atraso, com multa e juros

`fine` e `interest` usam os padrões do Stark (2% e 1% ao mês), então uma invoice paga
depois do vencimento seria creditada por mais que o valor emitido. O código trata isso —
lê o `amount` do evento de crédito, nunca o valor emitido, e guarda os dois em colunas
separadas.

**Tentei exercitar o caminho forçando invoices a vencer, e o sandbox não as paga.** Dos
logs reais:

```
53 invoices emitidas
14 chegaram a ficar overdue
 0 foram pagas depois de vencer   →  10 expiraram, 4 seguem overdue
 0 tiveram fine_amount ou interest_amount
```

O simulador paga em ~8 minutos ou não paga mais. Uma vez `overdue`, a invoice segue até
`expired` — não existe pagamento em atraso no sandbox.

Ou seja, `credited_amount_cents` sempre igualou `nominal_amount_cents` nesta execução.
A distinção entre as duas colunas é preparação para produção, não algo que os dados aqui
comprovem.

### Estorno de invoice já creditada

O sandbox reverte invoices pagas. Observado duas vezes:

```
reversing → sending → sent → reversed → voided     (amount vai a 0)
reversing → sending → sent → failed   → refunded   ('Receiver bank internal error')
```

**O sistema não trata isso.** Esses `log.type` caem no ramo de tipo desconhecido, o evento
é registrado em `webhook_events` e o `credited_at` permanece. Se a Transfer já tiver saído,
o dinheiro foi repassado a partir de um crédito que depois voltou.

Tratar exigiria decidir o que fazer com um repasse já concluído — estornar não é operação
do escopo do desafio. O que existe hoje é o registro: o evento fica salvo com o payload
cru, então a inconsistência é detectável.

### Tipos de log não mapeados

Sete tipos ocorreram no sandbox e não têm tratamento: `sending`, `sent`, `reversing`,
`reversed`, `refunded`, `failed` e `voided` — todos ligados aos dois fluxos acima. Eles
não quebram nada (são ignorados com aviso no log), mas também não atualizam o `status`.

## Testes

```bash
.venv/bin/python -m pytest tests/unit -q
```

**76 testes, ~0,6 segundo.** Rodam com Postgres e Redpanda desligados: os repositórios,
o client do Stark e o producer são fakes em memória, definidos em
[tests/unit/conftest.py](tests/unit/conftest.py). Nenhum teste toca banco, rede ou
relógio real.

| Arquivo | Testes | Cobre |
|---|---|---|
| [test_signature.py](tests/unit/test_signature.py) | 6 | validação da assinatura |
| [test_api.py](tests/unit/test_api.py) | 8 | borda HTTP: 400 vs 500 vs 200 |
| [test_receive_event.py](tests/unit/test_receive_event.py) | 17 | dedupe e roteamento de eventos |
| [test_process_credited.py](tests/unit/test_process_credited.py) | 10 | idempotência e cálculo do repasse |
| [test_business_rules.py](tests/unit/test_business_rules.py) | 13 | funções puras e gerador de CPF |
| [test_issue_invoices.py](tests/unit/test_issue_invoices.py) | 8 | emissão em lote |
| [test_reconcile.py](tests/unit/test_reconcile.py) | 12 | Fallback (casos A, B1 e B2) |

O plano completo está em [testes.md](testes.md).

### Os testes que sustentam as decisões

Cada decisão de projeto tem um teste que a torna executável — se alguém desfizer a
decisão, o teste quebra:

- **`test_payload_adulterado_e_rejeitado`** — a mesma assinatura com conteúdo diferente
  falha. Prova que a assinatura é *verificada*, não só lida.
- **`test_body_cru_chega_intacto_ao_verifier`** — usa um JSON com espaçamento irregular e
  assere que o que chega ao verifier difere do JSON re-serializado. Pega a armadilha do
  `request.json()`.
- **`test_commit_acontece_antes_da_chamada_ao_stark`** — assere a ordem exata
  `["commit", "stark", "commit"]`. Remover o primeiro commit reabre o furo em que um erro
  ao confirmar apagaria a linha `pending`.
- **`test_ordem_de_chegada_nao_muda_o_estado_final`** — processa `credited→paid` e
  `paid→credited` e exige estado idêntico (decisão 6).
- **`test_external_id_nao_usa_o_prefixo_invoice`** — trava a regressão que fez 5
  transferências falharem como *Duplicated transfer* (decisão 2).
- **`test_default_due_nao_vence_imediatamente`** — pega um `due` curto demais, que faria
  o sandbox nunca pagar nenhuma invoice.
- **`test_carencia_protege_trabalho_em_andamento`** — uma linha `pending` recém-criada
  não é tratada como órfã pelo Fallback.

## Validação da execução

O [scripts/validate.py](scripts/validate.py) confronta **três fontes independentes** — as
tabelas, os eventos recebidos e a API do Stark — e sai com código 1 se alguma discordar.

```bash
.venv/bin/python -m scripts.validate                     # relatório no terminal
.venv/bin/python -m scripts.validate --json saida.json   # e os dados brutos
```

Resultado da execução de 6 horas:

```
69 invoices em 7 lotes
54 creditadas  →  54 Transfers  →  54 success

líquido creditado:  R$ 29.031,13
total transferido:  R$ 29.031,13     diferença: 0
```

As 13 verificações passaram: nenhum crédito sem Transfer, nenhuma invoice com duas
Transfers, nenhum `external_id` repetido, nenhuma Transfer presa em `pending` ou recusada,
todos os eventos processados e sem duplicatas, e cada `credited` com seu
`transfer success` correspondente.

## Dev

Todos os comandos rodam **da raiz do projeto** e usam `-m`. Executar o arquivo direto
(`python app/entrypoints/issuer.py`) coloca `app/entrypoints/` no `sys.path` em vez da
raiz, e o `import app` falha.

### Preparar o ambiente

```bash
poetry install                               # dependências no .venv do projeto
docker compose up -d                         # Postgres, Redpanda, console e o setup da fila
.venv/bin/python -m scripts.create_tables    # cria as tabelas; idempotente
```

O `.env` precisa de `STARK_PROJECT_ID`, `STARK_PRIVATE_KEY_PATH`,
`STARK_DESTINATION_ACCOUNT_PATH` e `DATABASE_URL`. Para gerar um novo par de chaves:

```bash
.venv/bin/python -m scripts.generate_keys    # cole a pública no console do Stark
```

O tópico da fila é criado pelo serviço `redpanda-init` do compose.

### Entrypoints

```bash
# Invoice Generator — emite UM lote de 8 a 12 invoices e termina.
.venv/bin/python -m app.entrypoints.issuer

# Webhook — servidor HTTP, fica no ar
.venv/bin/python -m uvicorn app.entrypoints.api:app --port 8000 --reload

# Worker Transfer — consome a fila e cria as Transfers, fica no ar
.venv/bin/python -m app.entrypoints.worker

# Fallback — reconciliação, roda e termina
.venv/bin/python -m app.entrypoints.reconciler
```

### Agendamento da emissão

O entrypoint emite **um** lote e termina; quem repete é o serviço `issuer` do compose:

```yaml
INTERVALO_SEGUNDOS: "10800"   # 3 horas, como pede o enunciado
restart: unless-stopped
```

### Expor o webhook

O Stark exige HTTPS e não alcança `localhost`. Em outro terminal:

```bash
ngrok http 8000
```

Cadastre no console do Stark a URL **com o path**, com as subscriptions `invoice` **e**
`transfer` (ver decisão 7 — sem `transfer`, uma transferência recusada passa despercebida):

```
https://<subdominio>.ngrok-free.app/webhooks/stark
```

Teste antes de configurar — **400 é o resultado esperado**, porque significa que a
requisição chegou ao código e a assinatura foi corretamente rejeitada:

```bash
curl -X POST https://<subdominio>.ngrok-free.app/webhooks/stark -d '{}'
```

O inspetor em `http://localhost:4040` mostra cada entrega recebida, com body e headers.

### Interfaces web

| Endereço | O que é |
|---|---|
| `http://localhost:8080` | Redpanda Console — tópicos, mensagens e consumer groups |
| `http://localhost:4040` | inspetor do ngrok — cada requisição recebida do Stark |
| `http://localhost:8000/health` | a própria API |


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
- [knowledge.md](knowledge.md) — aprendizados

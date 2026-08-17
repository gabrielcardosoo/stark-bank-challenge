# Arquitetura

Diagrama: [stark-arquiteture.drawio.png](stark-arquiteture.drawio.png)
(fonte editável: [stark_arquiteture_v2.drawio](stark_arquiteture_v2.drawio))

## Fluxo geral

```
┌─ Meu ambiente ──────────────────────────────────────────────┐
│                                                             │
│  ┌─ Invoice Generator (cron 3h, 8 execuções) ─┐             │
│  │  gera 8-12 invoices (CPF válido)           │──────────────▶ Stark API
│  │  grava em invoices (status=created)        │             │
│  └────────────────────────────────────────────┘             │
│                                                             │
│  ┌─ Webhook (HTTP) ───────────────────────────┐             │
│  │  1. valida assinatura (event.parse)        │◀─────────────  Stark API
│  │  2. grava o evento; se já recebido, para   │             │   (pagamento)
│  │  3. credited? grava credited_at + valores  │             │
│  │     senão, só atualiza status              │             │
│  │  4. publica em invoices.credited           │──▶ Redpanda  │
│  │  5. responde 200  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─▶ Stark API
│  └────────────────────────────────────────────┘             │
│                                                             │
│  ┌─ Worker Transfer ──────────────────────────┐             │
│  │  consome invoices.credited (Redpanda)      │             │
│  │  net = invoice.amount - invoice.fee        │             │
│  │  Transfer(externalId = invoice.id)         │──────────────▶ Stark API
│  │  grava em transfers, commita offset        │             │
│  └────────────────────────────────────────────┘             │
│                                                             │
│  ┌─ Fallback (cron 30min) ────────────────────┐             │
│  │  A: event.query(isDelivered=false)         │──────────────▶ Stark API
│  │  B1: credited_at != NULL e sem transfer    │             │
│  │  B2: transfers em pending há > 5min        │             │
│  │  republica o que faltou                    │──▶ Redpanda  │
│  └────────────────────────────────────────────┘             │
│                                                             │
│  Postgres = sistema de registro                             │
└─────────────────────────────────────────────────────────────┘
```

Regra que organiza o desenho: **um único caminho gasta dinheiro**. Só o Worker Transfer
cria Transfer. Webhook e Fallback apenas colocam trabalho na fila.

## Componentes

### 1. Invoice Generator
Emite as faturas a cada 3 horas, de 8 a 12 por lote, para pessoas aleatórias. O sandbox
paga automaticamente parte delas.

- Tax ID precisa ter dígito verificador válido (Faker pt_BR) — o sandbox rejeita CPF inválido.
- Falha de um invoice não pode abortar o lote inteiro.
- Persistir o `stark_invoice_id` de tudo que for criado.

### 2. Webhook
Recebe o callback do Stark Bank. Responsabilidade deliberadamente mínima: **valida
assinatura, persiste, publica na fila, responde 200**. Nenhuma chamada à API do Stark
aqui — se travar, o Stark reentrega o evento no meio de uma transferência em andamento.

A assinatura é validada sobre o **body cru**, na borda HTTP ([api.py](app/entrypoints/api.py)).
Se o framework fizer parse do JSON e o código re-serializar, os bytes mudam e a
assinatura nunca bate. O service recebe o evento já autenticado.

O código de resposta separa dois mundos, e a distinção é regra de negócio:

| Falha | Status | Por quê |
|---|---|---|
| assinatura inválida/ausente, body não-JSON | **400** | é da requisição; reentregar nunca daria outro resultado |
| Postgres fora, bug interno | **500** | é nossa; 500 é o pedido explícito de reentrega |
| reentrega de evento já visto | **200** | não é erro |

Devolver 400 numa falha nossa descartaria um crédito para sempre, porque o Stark não
tentaria de novo.

**Os eventos chegam fora de ordem.** Medido no sandbox: `credited` antes de `paid` em
4 de 11 invoices. Por isso o crédito grava `credited_at` (coluna própria) e escreve em
`status` o mesmo `paid` que o evento `paid` escreveria — qualquer ordem de chegada
converge para o mesmo estado final. O evento `created` é ignorado para status: o issuer
já gravou a invoice na emissão. Ver decisão 6 do [README.md](README.md).

Persistir e publicar são duas escritas em sistemas diferentes, sem transação comum
(*dual write*). Se o INSERT der certo e o publish falhar, o evento fica órfão — o
Fallback é quem recupera. É a mitigação escolhida, e é por isso que ele existe.

### 3. Worker Transfer
Consome a fila `invoices.credited` e cria a Transfer para:

```
bank code:    20018183
branch:       0001
account:      6341320293482496
name:         Stark Bank S.A.
tax ID:       20.018.183/0001-80
account type: payment
```

Só recebe mensagens de `log.type == "credited"` — é o único que o webhook publica, porque
é nele que o dinheiro entra no saldo. Os valores usados (`amount` e `fee`) vêm desse
evento, que é o retrato do que efetivamente foi creditado: já inclui multa e juros de uma
invoice paga em atraso.

Os demais tipos (`paid`, `overdue`, `expired`, `canceled`) só atualizam o status e não
chegam ao Worker.

**Grava a intenção antes de mover o dinheiro.** A linha em `transfers` nasce como
`pending`, antes da chamada ao Stark:

```python
def process(invoice):
    external_id = transfer_external_id(invoice.id)
    net = net_amount(invoice.amount, invoice.fee)

    # 1. reivindica o trabalho — o UNIQUE decide quem ganha
    claimed = transfers.insert_pending(external_id, invoice.id, net)
    if not claimed:                    # ON CONFLICT DO NOTHING
        return                          # outro worker já pegou, ou já foi feito

    # 2. movimenta o dinheiro
    transfer = stark.create_transfer(amount=net, external_id=external_id, ...)

    # 3. confirma
    transfers.mark_created(external_id, transfer.id)
```

O INSERT funciona como mutex: dois workers na mesma mensagem, um ganha e o outro
desiste — **antes** de qualquer um chamar o Stark. A concorrência morre no Postgres, sem
depender de garantia remota.

E nunca existe um instante em que o sistema esqueceu que estava prestes a mover dinheiro:
o registro precede a ação.

O preço é uma janela nova. Como a linha é gravada **antes** da chamada, a existência dela
não prova que a Transfer saiu — quem prova é o `stark_transfer_id`, preenchido só no passo
3. Se o processo morrer depois de enviar a requisição e antes de registrar a resposta
(timeout, queda no meio da chamada), a linha fica em `pending` e o estado é ambíguo: pode
ter saído ou não.

É o caso B2 do Fallback, e a única forma de desfazer a ambiguidade é perguntar ao Stark.

**Offset commitado só depois** da Transfer criada e confirmada. A entrega é
at-least-once: a mesma mensagem pode chegar duas vezes, e quem impede a Transfer
duplicada é o `external_id`, não a fila.

### 4. Fallback
Faz **exatamente o que o webhook faria** — só que perguntando ao Stark em vez de esperar
ser avisado. Roda a cada 30 minutos e cobre dois tipos de falha diferentes.

**Caso A — o evento nunca chegou.** Servidor fora do ar, ngrok caiu, deploy em
andamento, endpoint devolveu 500. Não existe linha nenhuma no banco para consultar: só a
API do Stark sabe que aquele evento existiu. Daí o `event.query(isDelivered=false)`.

**Caso B — o evento chegou mas o processamento não terminou.** O webhook gravou e
respondeu 200, mas o publish na fila falhou; ou a mensagem chegou no Worker e a API de
Transfer estava fora. O invoice tem `credited_at` preenchido e o trabalho parou no meio.
São duas situações distintas, e **cada uma pede um tratamento diferente**:

- **B1 — invoice com `credited_at` e sem linha em `transfers`.** Localmente não aconteceu
  nada. Basta republicar na fila: o Worker vai reivindicar e seguir o fluxo normal.
  O critério é `credited_at`, nunca `status` — a coluna de status pode ser sobrescrita
  por um evento fora de ordem, e a rede de segurança não pode depender disso.
- **B2 — linha em `transfers` parada em `pending`.** Aqui você **não sabe** se a Transfer
  chegou a ser criada no Stark. Republicar na fila não resolve — o Worker tentaria o
  `insert_pending`, levaria conflito e desistiria, deixando a linha travada para sempre.
  A única saída é perguntar ao Stark por `external_id` e resolver: se a Transfer existe,
  marca `created`; se não existe, a chamada nunca saiu e o trabalho pode ser refeito.

```python
def reconcile():
    # A — o que nunca chegou
    for event in stark.events(is_delivered=False):
        if not webhook_events.exists(event.id):
            webhook_events.insert(event)              # mesma coisa que o webhook faz
            invoices.update_status(event.log.invoice)

        if event.log.type == "credited":
            fila.publish(event.log.invoice)

        stark.mark_delivered(event.id)                # tira da fila de não-entregues

    # B1 — creditado, nada foi reivindicado ainda
    for invoice in invoices.credited_without_transfer(older_than="5 min"):
        fila.publish(invoice)

    # B2 — reivindicado, mas não se sabe se saiu
    for row in transfers.pending(older_than="5 min"):
        transfer = stark.find_transfer_by_external_id(row.external_id)
        if transfer:
            transfers.mark_created(row.external_id, transfer.id)   # tinha saído
        else:
            transfers.release(row.external_id)                     # não saiu: libera
            fila.publish(row.invoice)                             # e refaz
```

> **Detalhe do SDK:** `starkbank.transfer.query()` filtra por `limit, after, before,
> transaction_ids, status, tax_id, sort, tags, ids` — **não existe filtro por
> `external_id`**, e `transfer.get()` só aceita o id do Stark. Por isso o
> [client](app/adapters/stark/client.py) repete o `external_id` dentro de `tags` na
> criação, e a busca do B2 vira `transfer.query(tags=[external_id])`. Sem essa tag, o
> B2 não tem como ser implementado.

Três detalhes que importam:

- **Marcar como entregue** (`event.update(id, is_delivered=True)`). Sem isso o mesmo
  evento reaparece na lista a cada 30 minutos para sempre. Não quebra nada — a
  idempotência segura —, mas a lista só cresce.
- **Carência de 5 minutos nos dois casos B.** Sem ela, o Fallback disputaria com o Worker
  um invoice que chegou agora e está sendo processado neste momento. Uma linha `pending`
  de 10 segundos não é um órfão — é trabalho em andamento.
- **Ele nunca cria a Transfer.** Só reinjeta trabalho na fila ou corrige o status de
  quem já foi criado.

O Fallback pode ser burro e agressivo justamente porque o Worker é idempotente. A divisão
é essa: **o Fallback garante que nada se perde, o `external_id` garante que nada duplica.**
Nenhum dos dois precisa ser esperto sozinho.

O webhook é a fonte primária; o polling é a fonte de verdade.

## Persistência

**Sistema de registro: Postgres.** A idempotência depende de duas coisas que só um banco
transacional entrega: constraint `UNIQUE` verificada na escrita, e
`INSERT ... ON CONFLICT DO NOTHING RETURNING` atômico — o statement que reivindica o
trabalho e informa quem ganhou numa operação só.

```sql
invoices(id, stark_invoice_id UNIQUE, customer_name, customer_tax_id,
         nominal_amount_cents, credited_amount_cents NULL, fee_cents NULL,
         transaction_ids VARCHAR[] NULL, status, due NULL,
         created_at, credited_at NULL)

webhook_events(id, stark_event_id UNIQUE, subscription, log_type,
               stark_invoice_id NULL, payload JSONB, received_at, processed_at NULL)

transfers(id, stark_transfer_id NULL, invoice_id FK, external_id UNIQUE,
          amount_cents, status, created_at, confirmed_at NULL)
```

- Dinheiro sempre em **centavos, inteiro**. Nunca `FLOAT`.
- `invoices.status` como ENUM espelhando **fielmente** o `invoice.status` do Stark:
  `created`, `paid`, `overdue`, `expired`, `canceled`. **Não existe `credited`** — é tipo
  de log, não status; no próprio evento de crédito o Stark reporta `status = 'paid'`.
- `invoices.credited_at` é o campo que decide se a invoice deve virar Transfer. Separar
  esse fato do `status` é o que torna o sistema imune à ordem de chegada dos eventos.
- `nominal_amount_cents` é o valor emitido; `credited_amount_cents` é o que entrou. Podem
  diferir por multa e juros numa invoice paga em atraso.
- `transaction_ids` só chega no evento `credited` — são as transações do extrato.
- `webhook_events.stark_invoice_id` é string solta, **sem FK**: o evento é persistido
  antes de qualquer consulta, e um evento de invoice que não seja nossa não pode derrubar
  o webhook. Correlação por join.
- `transfers.status`: `pending` → `created` (ou `failed`). A linha nasce `pending`, antes
  da chamada ao Stark — por isso `stark_transfer_id` é nulo até a confirmação.
- As constraints `UNIQUE` em `stark_event_id` e `external_id` **são regra de negócio**,
  não detalhe de schema. São elas que impedem pagamento em dobro sob concorrência.

O schema é criado por `python -m scripts.create_tables`, que chama
`Base.metadata.create_all`. Não há migrations: o `create_all` só cria o que falta e nunca
altera o que existe, então mudar um enum ou uma coluna exige dropar e recriar. Aceitável
enquanto não há dado a preservar; quem roda do zero não passa por isso.

### Por que existe a tabela webhook_events

`invoices` guarda o **estado atual**; `webhook_events` guarda o **que aconteceu**. A
segunda não se reconstrói a partir da primeira. Ela entrega quatro coisas:

1. **Dedupe barato e cedo** — a `UNIQUE` em `stark_event_id` rejeita uma reentrega antes
   de publicar na fila ou acordar o Worker. Não é a garantia final (essa é o
   `external_id`), mas custa um INSERT.
2. **O payload cru como evidência** — exatamente o que o Stark mandou, com assinatura
   validada e timestamp. Num sistema que move dinheiro é registro probatório, não
   conveniência.
3. **Replay** — se a lógica de processamento tiver um bug, você reprocessa os eventos
   guardados. Eventos já marcados como entregues não voltam pela API.
4. **Distinguir "não chegou" de "chegou e falhou"** — é exatamente o caso A vs. caso B do
   Fallback. Sem a tabela, não dá nem para formular a pergunta.

A fila não substitui isso: tem retenção limitada, não é consultável por id e não tem
constraint de unicidade. É transporte, não sistema de registro.

## Redpanda (fila)

- Tópico `invoices.credited`, **key = invoice_id** (garante ordem por invoice).
- Consumer group único, **commit manual** após processamento bem-sucedido.
- Entrega at-least-once. A idempotência não vem da fila — vem do `external_id`.
- Mensagem malformada não pode travar a partição: descartar para uma DLQ ou registrar
  e seguir.

Duas entradas na fila: o Webhook (caminho normal) e o Fallback (recuperação). Uma saída
só: o Worker Transfer.

## Decisões

Ver [README.md](README.md) — taxa descontada, 1:1 vs. agregação, Postgres como sistema de
registro, Redpanda vs. polling, vencimento das invoices, eventos fora de ordem e taxa zero
no sandbox.

## Comportamento observado no Sandbox

Dados medidos, não suposições — capturados no inspetor do ngrok e na API:

- **Pagamento em ~8 minutos.** Entre 493 e 504 segundos de `created` até `credited`, em 5
  invoices. Valida a janela de 7h escolhida na decisão 5.
- **`credited` antes de `paid` em 4 de 11 invoices.** Motivou a decisão 6.
- **Taxa zero.** `fee = 0` no evento e na transação gerada.
- **Reentrega real:** com a API fora do ar, cada evento apareceu duplicado. A idempotência
  não é precaução teórica.
- **Um invoice gera ~4 eventos** (`created`, `paid`, `credited`, ou `overdue`/`expired`).
  Com 8 lotes de ~10, são ~300 entregas em 24h.

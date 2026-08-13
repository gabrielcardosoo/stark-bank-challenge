# Arquitetura

## Fluxo geral

```
┌─ Issuer (cron a cada 3h, 8 execuções em 24h) ─┐
│  gera 8-12 invoices (CPF válido)              │──▶ Stark API
│  grava invoices (status=created)              │
└───────────────────────────────────────────────┘

┌─ Webhook endpoint ────────────────────────────┐
│  1. valida assinatura (event.parse)           │
│  2. INSERT INTO webhook_events (dedupe)       │
│  3. retorna 200  ◀── rápido, sem chamar API   │
└───────────────────────────────────────────────┘
                 │
┌─ Worker ───────▼──────────────────────────────┐
│  pega evento não processado                   │
│  se log.type == credited:                     │
│    net = invoice.amount - invoice.fee         │
│    Transfer(externalId = invoice.id)          │──▶ Stark API
│    grava transfers, marca evento processado   │
└───────────────────────────────────────────────┘

┌─ Reconciliação (periódica) ───────────────────┐
│  event.query(isDelivered=false)               │  ← rede de segurança
│  invoices credited sem transfer               │
└───────────────────────────────────────────────┘
```

## Componentes

### 1. Issuer de Invoices
Emite as faturas a cada 3 horas, de 8 a 12 por lote, para pessoas aleatórias. O sandbox
paga automaticamente parte delas.

Pontos de atenção:
- Tax ID precisa ter dígito verificador válido (usar Faker pt_BR) — o sandbox rejeita CPF inválido.
- Falha de um invoice não pode abortar o lote inteiro.
- Persistir o `stark_invoice_id` de tudo que for criado.

### 2. Webhook endpoint
Recebe o callback do Stark Bank sobre o pagamento do Invoice.

Responsabilidade é mínima e deliberada: **validar assinatura, persistir o evento, responder
200**. Nada de chamar a API do Stark aqui — se a chamada travar, o Stark reentrega o evento
no meio de uma transferência em andamento.

### 3. Worker
Consome os eventos persistidos e executa a Transfer para:

```
bank code:    20018183
branch:       0001
account:      6341320293482496
name:         Stark Bank S.A.
tax ID:       20.018.183/0001-80
account type: payment
```

Dispara **apenas** em `log.type == "credited"` — é nele que o dinheiro entra no saldo.
Os outros tipos (`created`, `paid`, `overdue`, `canceled`, `reversed`) apenas atualizam
o status do invoice.

### 4. Reconciliação
Webhook é *at-least-once*, mas não garantido: se o servidor estiver fora do ar, o evento
se perde. Um job periódico consulta `event.query(isDelivered=false)` e busca invoices
creditados sem Transfer correspondente.

O webhook é a fonte primária; o polling é a fonte de verdade.

## Banco (MySQL)

```sql
invoices(id, stark_invoice_id UNIQUE, customer_name, customer_tax_id,
         amount_cents, fee_cents, status, created_at, credited_at)

webhook_events(id, stark_event_id UNIQUE, subscription, log_type,
               payload JSON, processed_at, received_at)

transfers(id, stark_transfer_id, invoice_id FK, external_id UNIQUE,
          amount_cents, status, created_at)
```

- Dinheiro sempre em **centavos, `BIGINT`**. Nunca `FLOAT`.
- `status` como ENUM espelhando o ciclo de vida do Stark (`created`, `credited`, `paid`,
  `overdue`, `canceled`) — deixa a reconciliação trivial.
- As constraints `UNIQUE` em `stark_event_id` e `external_id` são o que garante
  idempotência sob concorrência. Não são detalhe de schema, são regra de negócio.

## Decisões em aberto

Ver [README.md](README.md) — taxa descontada na Transfer e 1:1 vs. agregação.

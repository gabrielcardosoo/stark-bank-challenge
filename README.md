# Stark Bank Challenge

Integração que emite Invoices periodicamente no Sandbox do Stark Bank e, ao receber o
callback de crédito, repassa o valor líquido via Transfer.

## Decisões de projeto

> Estas decisões precisam estar explícitas na entrega. As duas primeiras são ambiguidades
> reais do enunciado — o que conta não é acertar a interpretação "certa", é deixar claro
> que foi escolha consciente e não acidente.

### 1. Qual taxa é descontada do valor transferido

O enunciado pede para enviar o valor recebido *"minus eventual fees"*. Isso se refere à
taxa do **Invoice** (`invoice.fee`), que separa o valor pago pelo cliente do valor
efetivamente creditado no saldo. Porém a **Transfer também tem custo próprio**.

Consequência: transferindo exatamente `invoice.amount - invoice.fee`, o saldo pode ficar
levemente negativo, porque a taxa da Transfer sai por fora.

**Decisão:** _(preencher)_ — transferir `invoice.amount - invoice.fee`, aceitando o custo
da Transfer como despesa da operação; ou descontar também uma estimativa da taxa de
transferência.

### 2. Uma Transfer por Invoice, ou Transfers agregadas

Dá para criar uma Transfer para cada Invoice creditado, ou acumular os créditos e enviar
uma Transfer agregada por período.

**Decisão:** _(preencher — recomendado: 1 Invoice → 1 Transfer)_

Motivo: rastreabilidade direta entre entrada e saída, e idempotência natural — o id do
Invoice vira o `externalId` da Transfer, o que sozinho já impede pagamento duplicado. A
agregação economizaria taxas, mas exigiria controle de janela e tornaria a conciliação
bem mais difícil de provar.

### 3. Postgres como sistema de registro

A idempotência do desafio depende de duas garantias que só um banco transacional entrega:
constraint `UNIQUE` de verdade e `SELECT ... FOR UPDATE`.

São elas que impedem o pagamento em dobro sob concorrência — a `UNIQUE` em
`transfers.external_id` é regra de negócio, não detalhe de schema.

### 4. Kafka como fila entre webhook e worker

Desacopla o recebimento do processamento e dá durabilidade ao trabalho pendente.

Ponto de atenção: Kafka entrega **at-least-once**. A mesma mensagem pode chegar duas
vezes, e commit de offset antes do processamento perde trabalho. Portanto:

- Offset commitado **só depois** da Transfer criada e persistida.
- A idempotência não vem do Kafka — vem da constraint `UNIQUE` em `transfers.external_id`.

Alternativa mais simples, se quiser reduzir a infraestrutura: worker fazendo polling na
tabela `webhook_events` com `FOR UPDATE SKIP LOCKED`. Mesmas garantias, um componente a
menos.

## Documentos do projeto

- [arquiteture.md](arquiteture.md) — componentes e fluxo
- [testes.md](testes.md) — testes unitários obrigatórios
- [todos.md](todos.md) — checklist de implementação
- [knowledge.md](knowledge.md) — aprendizados

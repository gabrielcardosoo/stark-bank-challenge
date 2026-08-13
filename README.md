# Stark Bank Challenge

Integração que emite Invoices periodicamente no Sandbox do Stark Bank e, ao receber o
callback de crédito, repassa o valor líquido via Transfer.

## Decisões de projeto

> Estas duas decisões precisam estar explícitas na entrega. Ambas são ambiguidades reais
> do enunciado — o que conta não é acertar a interpretação "certa", é deixar claro que
> foi uma escolha consciente e não um acidente.

### 1. Qual taxa é descontada do valor transferido

O enunciado pede para enviar o valor recebido *"minus eventual fees"*. Isso se refere à
taxa do **Invoice** (`invoice.fee`), que é o que separa o valor pago pelo cliente do valor
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

## Documentos do projeto

- [arquiteture.md](arquiteture.md) — componentes e fluxo
- [testes.md](testes.md) — cenários de teste obrigatórios
- [todos.md](todos.md) — checklist de implementação
- [knowledge.md](knowledge.md) — aprendizados

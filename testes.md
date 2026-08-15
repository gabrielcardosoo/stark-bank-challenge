# Testes unitários — não esquecer

Só o que roda isolado: com fakes/mocks, sem banco real, sem rede, sem relógio real.

---

## 1. Validação da assinatura do webhook

Manter o `event.parse` atrás de uma interface (`SignatureVerifier`) para conseguir
injetar um fake e testar o handler isoladamente.

- [ ] **Assinatura válida** → handler processa e persiste o evento.
- [ ] **Assinatura inválida** → rejeita e **nenhuma Transfer é criada** (asserir no mock do client).
- [ ] **Header `Digital-Signature` ausente** → rejeita com 4xx; não pode estourar exception não tratada e virar 500.
- [ ] **Payload adulterado com assinatura original** → verificação falha. É o teste que prova que a assinatura está sendo verificada, e não só lida.
- [ ] **Corpo malformado / JSON inválido** → 4xx controlado, sem 500.

> Armadilha: validar contra o **body cru (raw)**. Se o framework fizer parse do JSON e
> você re-serializar, os bytes mudam e a assinatura nunca bate. Vale um teste com um body
> que muda ao re-serializar (ordem de chaves ou espaçamento diferente).

## 2. Idempotência (não mandar 2 Transfers para o mesmo Invoice)

Ponto mais crítico do desafio. Com repositório fake em memória.

- [ ] **Mesmo `event.id` processado 2x** → 1 registro, 1 chamada de Transfer.
- [ ] **Dois `event.id` diferentes para o mesmo Invoice** → o `external_id` derivado do invoice id impede a duplicata. Dedupe por evento sozinha não cobre este caso.
- [ ] **`insert_pending` já existe** (outro worker reivindicou) → retorna sem chamar o Stark. É o mutex que impede a corrida.
- [ ] **Falha entre reivindicar e chamar o Stark** → a linha fica `pending`; ao reprocessar pelo Kafka, o Worker desiste no conflito e **não** cria uma segunda Transfer.
- [ ] **Falha entre criar a Transfer e confirmar** → linha continua `pending` mesmo com a Transfer criada no Stark; nenhuma segunda chamada acontece.
- [ ] **Evento já marcado como processado** → no-op, responde 200.
- [ ] **`external_id` é determinístico** — mesma entrada gera sempre o mesmo valor. É o que sustenta todos os itens acima.

## 3. Roteamento de eventos

- [ ] `log.type == "credited"` → **cria** Transfer.
- [ ] `log.type` em `created`, `paid`, `overdue`, `canceled`, `reversed` → **não cria** Transfer, mas atualiza o status do invoice.
- [ ] `log.type` desconhecido → ignora sem quebrar.

## 4. Cálculo do valor líquido

Função pura, os testes mais baratos do projeto.

- [ ] `net = amount - fee`, em **centavos, inteiro**. Nenhum float no caminho.
- [ ] `fee == 0` → valor cheio.
- [ ] `fee >= amount` → **não** gera Transfer de valor zero ou negativo.
- [ ] Valor persistido == valor enviado na Transfer.

## 5. Emissor de Invoices

- [ ] Quantidade sorteada fica sempre entre **8 e 12** — rodar o gerador N vezes e asserir os limites (nunca 7, nunca 13).
- [ ] CPF/CNPJ gerado tem **dígito verificador válido** — validar com uma implementação independente do gerador.
- [ ] **Falha parcial**: com o client mockado falhando em 3 de 10, as outras 7 são criadas e as falhas ficam registradas — o lote não aborta inteiro.
- [ ] Todo invoice criado é persistido com o `stark_invoice_id` retornado.

## 6. Worker e reconciliação

- [ ] **Retry com backoff**: client falha N vezes → asserir número de tentativas e intervalos (com clock fake), e que desiste deixando o evento reprocessável.
- [ ] **Falha do worker não vira 5xx**: o endpoint já respondeu 200; o erro fica contido no processamento.
- [ ] **B1 — seleciona os certos**: dado um conjunto em memória, retorna só os invoices `credited` sem linha em `transfers`.
- [ ] **B2 — `pending` que já saiu no Stark** → marca `created`, **não** republica no Kafka.
- [ ] **B2 — `pending` que não saiu no Stark** → libera a linha e republica.
- [ ] **Carência respeitada**: linha `pending` de 10 segundos **não** é tratada como órfã (senão o Fallback disputa com o Worker).
- [ ] **Reconciliação é idempotente**: rodar duas vezes seguidas não gera Transfers extras.

## 7. Kafka (com producer/consumer fakes)

Kafka entrega at-least-once — estes testes existem para provar que o sistema aguenta isso.

- [ ] **Mesma mensagem consumida 2x** → 1 Transfer. É o mesmo cenário da seção 2, agora entrando pelo caminho do Kafka.
- [ ] **Mensagem publicada com `key = invoice_id`** — é o que garante ordem por invoice na partição.
- [ ] **Offset só é commitado após sucesso**: fazer o processamento lançar exception e asserir que o commit **não** aconteceu.
- [ ] **Falha ao publicar não derruba o webhook**: producer fake lançando exception → endpoint ainda responde 200 e o evento fica persistido para a reconciliação recuperar.
- [ ] **Mensagem malformada não trava o consumer**: payload inválido → registra/descarta e segue para a próxima, sem travar a partição.

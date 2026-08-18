# Aprendizados com o projeto

## Sobre o Stark Bank como produto

Compreendi como o Stark Bank inova o mercado, com uma solução focada no B2B. A ideia é
facilitar os diversos processos de uma empresa — pagamento de funcionários, de
fornecedores — conectando-se aos ERPs já existentes.

## Sobre o SDK

**Não existe objeto client.** O SDK tem uma variável de módulo `starkbank.user` e todas
as funções aceitam `user=` como último parâmetro. Passar explicitamente evita estado
global compartilhado; o `StarkClient` do projeto é o client que o SDK não oferece.

**A autenticação é criptográfica, não bearer token.** Cada requisição é assinada com a
chave privada EC. Foi o argumento decisivo para usar o SDK em vez de falar HTTP direto:
implementar ECDSA sobre secp256k1 na mão é trabalho que o desafio não avalia, com risco
de erro sutil.

**`transfer.query()` não filtra por `external_id`.** Os filtros são `limit, after,
before, transaction_ids, status, tax_id, sort, tags, ids`. Por isso o `external_id` é
repetido em `tags` na criação — sem isso, o Fallback não conseguiria descobrir se uma
Transfer `pending` chegou a sair.

**Os docstrings estão desatualizados em pelo menos um ponto:** dizem que `invoice.status`
vale `"registered"`, mas os logs reais mostram `created`. Verificar contra a API vale
mais que ler a documentação.

## Sobre webhooks (o aprendizado central)

**A assinatura é por recurso, não por tipo de log.** Assinando `invoice`, chegam todos os
logs: `created`, `paid`, `credited`, `overdue`, `expired`, `canceled`. Não há filtro.

**Os eventos chegam fora de ordem.** Medido: `credited` antes de `paid` em 4 de 11
invoices. Foi o achado mais importante do projeto — ver a decisão 6 do README. A lição
geral: *nunca assumir que a ordem de chegada reflete a ordem dos fatos*. Estado derivado
de eventos precisa ser idempotente e comutativo, ou usar colunas que não competem.

**Sem resposta, o Stark reentrega.** Com a API fora do ar, cada evento apareceu duplicado
no inspetor do ngrok. A idempotência não é precaução teórica — a reentrega acontece na
primeira falha.

**`fee` só existe no evento `credited`.** Em `created` e `paid` ele vale 0. Calcular
`amount - fee` em qualquer outro evento transferiria o valor errado.

**O envelope é sempre igual.** O objeto `invoice` vem completo em todo evento; o que muda
são os valores (`status`, `fee`, `transactionIds`). O JSON cru usa camelCase
(`nominalAmount`), o SDK converte para snake_case (`nominal_amount`) — importa ao
consultar a coluna `payload` em JSONB.

**O `external_id` da Transfer não pode ser `invoice-{id}`.** O Stark usa essa string
internamente para o lançamento do crédito da invoice, e uma Transfer com o mesmo
identificador é recusada com *"Duplicated transfer"* — mesmo sem nenhuma Transfer
anterior com aquele id. Custou horas para diagnosticar porque a mensagem de erro aponta
para duplicidade, e o instinto é procurar uma transferência repetida que não existe.

O teste que isolou a causa:

| `external_id` | resultado |
|---|---|
| `invoice-{id}` de invoice real | **failed** |
| `invoice-9999999999999999` (id inexistente) | success |
| `invoice-{id}-v2` | success |
| mesmo valor, outro prefixo | success |
| `transfer-{id}` | success |

**A recusa é assíncrona.** A criação devolve `status=created` sem erro; a falha chega
segundos ou minutos depois. Nenhum `try/except` na chamada pega isso — só o webhook de
`transfer`. Vale para "Duplicated transfer" e para "Insufficient balance".

## Sobre o Sandbox

**Paga em ~8 minutos.** Medido em 5 invoices: entre 493 e 504 segundos entre `created` e
`credited`. Foi o dado que validou a escolha de `due` de 1h.

**A taxa é zero.** Confirmado no evento e na transação gerada. O desconto está
implementado mas não subtrai nada neste ambiente.

**O Project pode ter restrição de IP.** O erro `invalidIp` bloqueia todas as chamadas de
saída, e o IP residencial muda — vale remover a restrição em vez de listar um IP.

## Sobre arquitetura

**Um único caminho gasta dinheiro.** Só o Worker cria Transfer; webhook e Fallback apenas
colocam trabalho na fila. Dois caminhos significariam dois lugares para garantir
idempotência e uma corrida entre eles.

**O Fallback garante que nada se perde; o `external_id` garante que nada duplica.**
Nenhum dos dois precisa ser esperto sozinho — é a divisão que permite o Fallback ser
burro e agressivo.

**Duas janelas de falha diferentes exigem duas barreiras.** A constraint `UNIQUE` local
cobre a reentrega quando a linha já existe; o `externalId` no Stark cobre o intervalo
entre criar a Transfer e persisti-la. Não é redundância.

**`create_all` não altera o que já existe.** Ao remover `credited` do enum foi preciso
dropar as tabelas e recriar — o custo de ter dispensado migrations. Aceitável enquanto
não há dado a preservar.

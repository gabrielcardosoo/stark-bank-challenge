# TODOs

## Setup
- [ ] Criar Webhook endpoint (subscription `invoice`) apontando para a URL pública
- [ ] URL pública para dev local (ngrok / cloudflared)
- [ ] Docker Compose: api, worker, issuer, reconciler, postgres/mysql, kafka
## Implementação
- [ ] Schema do banco (ver [arquiteture.md](arquiteture.md)) com as constraints UNIQUE
- [ ] `rules.py`: valor líquido, `external_id` determinístico, gerador de CPF válido
- [ ] Issuer: 8-12 invoices, a cada 3h, tax ID válido
- [ ] Webhook: valida assinatura sobre o **body cru**, persiste, publica no Kafka, responde 200
- [ ] Producer/consumer Kafka (tópico `invoices.credited`, key = invoice_id)
- [ ] Worker: consome, cria Transfer com `externalId`, commita offset **depois** do sucesso
- [ ] Retry com backoff nas chamadas à API
- [ ] Reconciliação (`event.query(isDelivered=false)` + invoices credited sem transfer)
- [ ] Endpoint `/health`
- [ ] Validação das env vars obrigatórias no boot

## Antes de entregar
- [ ] Preencher as decisões 1 e 2 do [README.md](README.md)
- [ ] Rodar o checklist de [testes.md](testes.md)
- [ ] Rodar 24h completas no sandbox e conferir a soma final

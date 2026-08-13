# TODOs

## Setup
- [ ] Criar `.gitignore` cobrindo `.env`, `*.pem`, `*.key` — **antes** do primeiro `git add`
- [ ] Criar Project no sandbox e guardar a private key em variável de ambiente
- [ ] Criar Webhook endpoint (subscription `invoice`) apontando para a URL pública
- [ ] URL pública para dev local (ngrok / cloudflared)
- [ ] Docker Compose (app + MySQL)

## Implementação
- [ ] Schema do banco (ver [arquiteture.md](arquiteture.md)) com as constraints UNIQUE
- [ ] Issuer: 8-12 invoices, a cada 3h, tax ID válido
- [ ] Webhook: valida assinatura sobre o **body cru**, persiste, responde 200
- [ ] Worker: processa `credited`, cria Transfer com `externalId`
- [ ] Retry com backoff nas chamadas à API
- [ ] Job de reconciliação (`event.query(isDelivered=false)`)
- [ ] Endpoint `/health`
- [ ] Validação das env vars obrigatórias no boot

## Antes de entregar
- [ ] Preencher as duas decisões em aberto no [README.md](README.md)
- [ ] Rodar o checklist de [testes.md](testes.md)
- [ ] Rodar 24h completas no sandbox e conferir a soma final

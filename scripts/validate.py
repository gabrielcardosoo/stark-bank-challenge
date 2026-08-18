"""Relatório de conciliação: prova que cada crédito virou exatamente uma Transfer.

Compara três fontes independentes — o banco local, os eventos recebidos e a API do
Stark — e falha (exit 1) se alguma delas discordar.

    python -m scripts.validate                 # relatório no terminal
    python -m scripts.validate --json arquivo  # e também um JSON com os dados brutos
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--json", metavar="ARQUIVO",
                    help="grava os dados da execução num JSON")
args = parser.parse_args()

relatorio: dict = {"gerado_em": datetime.now(timezone.utc).isoformat(), "verificacoes": []}

import starkbank
from sqlalchemy import text

from app.adapters.db.connector import session_scope
from app.adapters.stark import StarkClient

OK, ERRO = "OK  ", "FALHA"
problemas = []


def checar(condicao: bool, titulo: str, detalhe: str = "") -> None:
    print(f"[{OK if condicao else ERRO}] {titulo}{(' — ' + detalhe) if detalhe else ''}")
    relatorio["verificacoes"].append(
        {"titulo": titulo, "passou": bool(condicao), "detalhe": detalhe or None})
    if not condicao:
        problemas.append(titulo)


def secao(titulo: str) -> None:
    print(f"\n{titulo}\n{'-' * len(titulo)}")


with session_scope() as s:
    q = lambda sql: s.execute(text(sql)).all()

    secao("1. Volume emitido")
    total, = q("SELECT count(*) FROM invoices")[0]
    por_status = q("SELECT status, count(*) FROM invoices GROUP BY status ORDER BY 2 DESC")
    print(f"    {total} invoices emitidas: " + ", ".join(f"{n} {st}" for st, n in por_status))
    lotes = q("""SELECT count(*) FROM (
                   SELECT date_trunc('hour', created_at) FROM invoices
                   GROUP BY 1) x""")[0][0]
    print(f"    distribuídas em {lotes} lotes")
    checar(total > 0, "houve emissão")
    relatorio["emissao"] = {"total_invoices": total, "lotes": lotes,
                            "por_status": {st: n for st, n in por_status}}

    secao("2. Todo crédito virou Transfer")
    creditadas, = q("SELECT count(*) FROM invoices WHERE credited_at IS NOT NULL")[0]
    com_transfer, = q("""SELECT count(DISTINCT i.id) FROM invoices i
                           JOIN transfers t ON t.invoice_id = i.id
                          WHERE i.credited_at IS NOT NULL""")[0]
    print(f"    {creditadas} creditadas, {com_transfer} com Transfer")
    relatorio["credito"] = {"creditadas": creditadas, "com_transfer": com_transfer}
    checar(creditadas == com_transfer, "nenhum crédito ficou sem Transfer",
           f"{creditadas - com_transfer} órfã(s)" if creditadas != com_transfer else "")

    secao("3. Uma Transfer por Invoice (sem duplicidade)")
    duplicadas = q("""SELECT invoice_id, count(*) FROM transfers
                      GROUP BY invoice_id HAVING count(*) > 1""")
    checar(not duplicadas, "nenhuma invoice gerou mais de uma Transfer",
           f"{len(duplicadas)} com duplicata" if duplicadas else "")
    ext_repetidos = q("""SELECT external_id, count(*) FROM transfers
                         GROUP BY external_id HAVING count(*) > 1""")
    checar(not ext_repetidos, "external_id único em todas as Transfers")

    secao("4. Conciliação financeira")
    liquido, = q("""SELECT coalesce(sum(credited_amount_cents - coalesce(fee_cents,0)),0)
                      FROM invoices WHERE credited_at IS NOT NULL""")[0]
    transferido, = q("""SELECT coalesce(sum(amount_cents),0) FROM transfers
                         WHERE status IN ('success','created','processing')""")[0]
    print(f"    líquido creditado: {liquido:>12,} centavos  (R$ {liquido/100:,.2f})")
    print(f"    total transferido: {transferido:>12,} centavos  (R$ {transferido/100:,.2f})")
    # sum() do Postgres devolve Decimal, que o json não serializa
    relatorio["conciliacao"] = {"liquido_creditado_centavos": int(liquido),
                                "total_transferido_centavos": int(transferido),
                                "diferenca_centavos": int(liquido - transferido)}
    checar(liquido == transferido, "os valores fecham",
           f"diferença de {liquido - transferido} centavos" if liquido != transferido else "")

    secao("5. Estado final das Transfers")
    linhas_tr = q("""SELECT status, count(*), sum(amount_cents) FROM transfers
                     GROUP BY status ORDER BY 2 DESC""")
    for st, n, soma in linhas_tr:
        print(f"    {n:>4} {st:<12} {soma:>12,} centavos")
    relatorio["transfers"] = {st: {"quantidade": n, "total_centavos": int(soma)}
                              for st, n, soma in linhas_tr}
    pend, = q("SELECT count(*) FROM transfers WHERE status = 'pending'")[0]
    falhas, = q("SELECT count(*) FROM transfers WHERE status = 'failed'")[0]
    checar(pend == 0, "nenhuma Transfer presa em pending", f"{pend} pendente(s)" if pend else "")
    checar(falhas == 0, "nenhuma Transfer recusada", f"{falhas} falha(s)" if falhas else "")

    secao("6. Eventos recebidos (só das invoices deste banco)")
    # eventos de `invoice` casam por stark_invoice_id; os de `transfer` casam pelo
    # externalId dentro do payload, que aponta para a invoice de origem
    NOSSOS = """
        SELECT * FROM webhook_events w
         WHERE (w.subscription = 'invoice'
                AND w.stark_invoice_id IN (SELECT stark_invoice_id FROM invoices))
            OR (w.subscription = 'transfer'
                AND w.payload->'event'->'log'->'transfer'->>'externalId'
                    IN (SELECT external_id FROM transfers))
    """
    linhas_ev = q(f"""SELECT subscription, log_type, count(*)
                        FROM ({NOSSOS}) x GROUP BY 1,2 ORDER BY 1,3 DESC""")
    for sub, tipo, n in linhas_ev:
        print(f"    {sub:<10} {tipo:<12} {n:>4}")
    relatorio["eventos"] = {f"{sub}.{tipo}": n for sub, tipo, n in linhas_ev}

    alheios, = q(f"SELECT count(*) FROM webhook_events "
                 f"WHERE stark_event_id NOT IN (SELECT stark_event_id FROM ({NOSSOS}) y)")[0]
    relatorio["eventos_de_outras_execucoes"] = alheios
    if alheios:
        print(f"    ({alheios} evento(s) de invoices que não estão neste banco — ignorados)")

    nao_proc, = q(f"SELECT count(*) FROM ({NOSSOS}) x WHERE processed_at IS NULL")[0]
    checar(nao_proc == 0, "todos os eventos foram processados",
           f"{nao_proc} sem processar" if nao_proc else "")
    dup_ev = q(f"""SELECT stark_event_id FROM ({NOSSOS}) x
                   GROUP BY 1 HAVING count(*) > 1""")
    checar(not dup_ev, "nenhum evento duplicado (dedupe funcionou)")

    creditos = q(f"SELECT count(*) FROM ({NOSSOS}) x "
                 f"WHERE subscription='invoice' AND log_type='credited'")[0][0]
    sucessos = q(f"SELECT count(*) FROM ({NOSSOS}) x "
                 f"WHERE subscription='transfer' AND log_type='success'")[0][0]
    checar(creditos == sucessos,
           "cada crédito teve um 'transfer success' correspondente",
           f"{creditos} créditos x {sucessos} sucessos" if creditos != sucessos else "")

    ext_locais = {r[0] for r in q("SELECT external_id FROM transfers WHERE status='success'")}
    ext_todos = {r[0] for r in q("SELECT external_id FROM transfers")}

secao("7. Confronto com a API do Stark (só as Transfers deste banco)")
c = StarkClient()
todas_stark = list(starkbank.transfer.query(user=c._project, limit=200))
nossas_stark = [t for t in todas_stark if t.external_id in ext_todos]
alheias = len(todas_stark) - len(nossas_stark)

por_status = Counter(t.status for t in nossas_stark)
print(f"    Transfers deste banco na conta: {len(nossas_stark)} — " +
      ", ".join(f"{n} {st}" for st, n in por_status.most_common()))
if alheias:
    print(f"    ({alheias} outra(s) na conta, de execuções anteriores — ignoradas)")
relatorio["stark"] = {"transfers_desta_execucao": len(nossas_stark),
                      "por_status": dict(por_status),
                      "outras_na_conta": alheias}

checar(len(nossas_stark) == len(ext_todos),
       "toda Transfer local existe no Stark",
       f"{len(ext_todos) - len(nossas_stark)} não encontrada(s)"
       if len(nossas_stark) != len(ext_todos) else "")

sucesso_stark = {t.external_id for t in nossas_stark if t.status == "success"}
faltando = ext_locais - sucesso_stark
checar(not faltando, "toda Transfer 'success' local é success no Stark",
       f"{len(faltando)} divergente(s)" if faltando else "")

recusadas = [t.external_id for t in nossas_stark if t.status in ("failed", "canceled")]
checar(not recusadas, "nenhuma Transfer deste banco foi recusada",
       f"{len(recusadas)}: {recusadas[:3]}" if recusadas else "")

saldo = starkbank.balance.get(user=c._project).amount
print(f"    saldo atual: {saldo:,} centavos (R$ {saldo/100:,.2f})")
relatorio["stark"]["saldo_centavos"] = saldo

relatorio["resultado"] = "todas as verificações passaram" if not problemas else "falhou"
relatorio["falhas"] = problemas

if args.json:
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, indent=2, ensure_ascii=False)
    print(f"\nJSON gravado em {args.json}")

print()
if problemas:
    print(f"RESULTADO: {len(problemas)} verificação(ões) falharam")
    for p in problemas:
        print(f"  - {p}")
    sys.exit(1)
print("RESULTADO: todas as verificações passaram")

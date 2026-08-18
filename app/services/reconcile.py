"""Rede de segurança: recupera o que o caminho normal deixou passar.

Cobre três lacunas, e a primeira é a que só ele enxerga:

  A  — o evento nunca chegou. Servidor fora do ar, túnel caído, deploy em andamento.
       Não existe linha nenhuma no banco para consultar: só a API do Stark sabe que
       aquele evento existiu.
  B1 — o crédito foi registrado mas ninguém reivindicou a Transfer (o publish na fila
       falhou).
  B2 — a Transfer foi reivindicada e ficou em `pending`: não se sabe se chegou a sair.

O caso A reaproveita o `ReceiveEvent` de propósito — é literalmente o mesmo tratamento
que o webhook daria, então não há como os dois caminhos divergirem.

Nunca cria Transfer: só reinjeta trabalho na fila ou corrige status.
"""
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

CARENCIA_PADRAO = timedelta(minutes=5)


class Reconcile:
    def __init__(self, stark, invoices, transfers, producer, receive_event, serializar):
        self._stark = stark
        self._invoices = invoices
        self._transfers = transfers
        self._producer = producer
        self._receive_event = receive_event
        self._serializar = serializar

    def execute(self, carencia: timedelta = CARENCIA_PADRAO) -> dict:
        """Roda os três casos e devolve o que foi recuperado em cada um."""
        return {
            "A": self._caso_a(),
            "B1": self._caso_b1(carencia),
            "B2": self._caso_b2(carencia),
        }

    # --- A: o evento nunca chegou ---------------------------------------------------

    def _caso_a(self) -> int:
        recuperados = 0

        for event in self._stark.undelivered_events():
            event_id = str(event.id)
            try:
                # mesmo tratamento do webhook: persiste, atualiza e enfileira
                self._receive_event.execute(event, self._serializar(event))
            except Exception:
                logger.exception(
                    "falha ao recuperar evento %s — fica não entregue para a próxima "
                    "rodada",
                    event_id,
                )
                continue

            # só depois de processar com sucesso: enquanto não marcar, o evento volta
            # na próxima consulta, o que é a garantia de não perder nada
            self._stark.mark_delivered(event_id)
            recuperados += 1
            logger.warning("caso A: evento %s recuperado do Stark", event_id)

        return recuperados

    # --- B1: creditada, sem ninguém ter reivindicado ---------------------------------

    def _caso_b1(self, carencia: timedelta) -> int:
        orfas = self._invoices.credited_without_transfer(carencia)

        for invoice in orfas:
            self._producer.publish(
                stark_invoice_id=invoice.stark_invoice_id,
                amount=invoice.credited_amount_cents,
                fee=invoice.fee_cents,
                event_id=f"reconcile-{invoice.stark_invoice_id}",
            )
            logger.warning(
                "caso B1: invoice %s creditada sem transfer — republicada",
                invoice.stark_invoice_id,
            )

        return len(orfas)

    # --- B2: reivindicada, desfecho desconhecido -------------------------------------

    def _caso_b2(self, carencia: timedelta) -> int:
        resolvidas = 0

        for linha in self._transfers.pending(carencia):
            transfer = self._stark.find_transfer_by_external_id(linha.external_id)

            if transfer is not None:
                # tinha saído: só faltou registrar
                self._transfers.mark_created(linha.external_id, transfer.id)
                logger.warning(
                    "caso B2: transfer %s existia no Stark (id=%s) — confirmada",
                    linha.external_id,
                    transfer.id,
                )
            else:
                # a chamada nunca chegou a sair: libera para o B1 reinjetar na próxima
                self._transfers.release(linha.external_id)
                logger.warning(
                    "caso B2: transfer %s não existe no Stark — liberada para refazer",
                    linha.external_id,
                )

            resolvidas += 1

        return resolvidas

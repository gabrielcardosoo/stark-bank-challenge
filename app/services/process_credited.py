"""Cria a Transfer do valor líquido de uma invoice creditada.

É o **único** ponto do sistema que move dinheiro. Webhook e Fallback apenas colocam
trabalho na fila; quem gasta é aqui.

O `commit` no meio do fluxo é deliberado: a reivindicação precisa estar gravada em
definitivo antes da chamada ao Stark. Sem ele, um erro ao confirmar desfaria também a
linha `pending`, e o Fallback perderia o rastro de uma Transfer que já pode ter saído.

Não importa `starkbank` nem o ORM: recebe as dependências prontas do entrypoint.
"""
import logging

from app.business_rules import is_transferable, net_amount, transfer_external_id

logger = logging.getLogger(__name__)


class ProcessCredited:
    def __init__(self, invoices, transfers, stark, commit):
        self._invoices = invoices
        self._transfers = transfers
        self._stark = stark
        self._commit = commit

    def execute(self, mensagem: dict) -> bool:
        """Processa uma mensagem da fila. True se a Transfer foi criada e confirmada.

        Depois do primeiro `commit`, qualquer erro sobe para o entrypoint: a linha
        `pending` já está gravada, e quem resolve é o Fallback (caso B2).
        """
        stark_invoice_id = mensagem["stark_invoice_id"]
        external_id = transfer_external_id(stark_invoice_id)

        invoice = self._invoices.get_by_stark_id(stark_invoice_id)
        if invoice is None:
            logger.error(
                "invoice %s não existe no banco — mensagem descartada", stark_invoice_id
            )
            return False

        net = net_amount(mensagem["amount"], mensagem["fee"])
        if not is_transferable(net):
            logger.warning(
                "invoice %s tem líquido %d — nada a transferir", stark_invoice_id, net
            )
            return False

        # reivindica ANTES de chamar o Stark: o UNIQUE decide quem ganha
        if not self._transfers.insert_pending(external_id, invoice.id, net):
            return False
        
        # grava a reivindicação em definitivo. Daqui para frente, um erro deixa a linha
        # `pending` no banco em vez de apagá-la junto.
        self._commit()

        transfer = self._obter_transfer(external_id, net)

        self._transfers.mark_created(external_id, transfer.id)
        
        self._commit()
        return True

    def _obter_transfer(self, external_id: str, net: int):
        """Cria a Transfer — a menos que ela já exista no Stark.

        O Stark **não** deduplica por `external_id`: verificado no sandbox, duas
        chamadas com o mesmo valor criam duas Transfers. Como a única barreira é local,
        perguntamos antes de gastar.
        """
        existente = self._stark.find_transfer_by_external_id(external_id)
        if existente is not None:
            logger.warning(
                "Transfer %s já existia no Stark (id=%s) — reaproveitando",
                external_id,
                existente.id,
            )
            return existente

        return self._stark.create_transfer(amount=net, external_id=external_id)

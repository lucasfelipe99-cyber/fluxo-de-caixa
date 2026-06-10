from enum import Enum


class StatusCadastro(str, Enum):
    ativo = "Ativo"
    inativo = "Inativo"


class TipoCategoria(str, Enum):
    receita = "Receita"
    despesa = "Despesa"


class StatusPagar(str, Enum):
    aberto = "Aberto"
    pago = "Pago"
    parcial = "Parcialmente Pago"
    cancelado = "Cancelado"


class StatusReceber(str, Enum):
    aberto = "Em aberto"
    recebido = "Recebido"
    parcial = "Parcialmente Recebido"
    cancelado = "Cancelado"


class OrigemLancamento(str, Enum):
    manual = "Manual"
    xml = "XML"


class TipoBaixa(str, Enum):
    pagar = "Pagar"
    receber = "Receber"

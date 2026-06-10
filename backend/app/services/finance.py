from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import extract, select
from sqlalchemy.orm import Session

from app.models.entities import BaixaFinanceira, Categoria, ContaPagar, ContaReceber
from app.models.enums import StatusPagar, StatusReceber, TipoBaixa, TipoCategoria
from app.schemas.common import BaixaPayload


def baixar_conta_pagar(db: Session, conta: ContaPagar, payload: BaixaPayload) -> ContaPagar:
    if conta.status == StatusPagar.cancelado:
        raise HTTPException(400, "Conta cancelada não pode receber baixa.")
    restante = Decimal(conta.valor) - Decimal(conta.valor_pago or 0)
    if payload.valor <= 0 or payload.valor > restante:
        raise HTTPException(400, "Valor da baixa inválido.")
    conta.valor_pago = Decimal(conta.valor_pago or 0) + payload.valor
    conta.status = StatusPagar.pago if conta.valor_pago >= conta.valor else StatusPagar.parcial
    db.add(BaixaFinanceira(
        empresa_id=conta.empresa_id,
        tipo=TipoBaixa.pagar,
        conta_pagar_id=conta.id,
        data_baixa=payload.data_baixa,
        valor=payload.valor,
        observacao=payload.observacao,
    ))
    db.commit()
    db.refresh(conta)
    return conta


def baixar_conta_receber(db: Session, conta: ContaReceber, payload: BaixaPayload) -> ContaReceber:
    if conta.status == StatusReceber.cancelado:
        raise HTTPException(400, "Conta cancelada não pode receber baixa.")
    restante = Decimal(conta.valor) - Decimal(conta.valor_recebido or 0)
    if payload.valor <= 0 or payload.valor > restante:
        raise HTTPException(400, "Valor da baixa inválido.")
    conta.valor_recebido = Decimal(conta.valor_recebido or 0) + payload.valor
    conta.status = StatusReceber.recebido if conta.valor_recebido >= conta.valor else StatusReceber.parcial
    db.add(BaixaFinanceira(
        empresa_id=conta.empresa_id,
        tipo=TipoBaixa.receber,
        conta_receber_id=conta.id,
        data_baixa=payload.data_baixa,
        valor=payload.valor,
        observacao=payload.observacao,
    ))
    db.commit()
    db.refresh(conta)
    return conta


def resumo_pagar(contas: list[ContaPagar]) -> dict[str, Decimal]:
    hoje = date.today()
    aberto = vencido = vencer = pago = Decimal("0")
    for conta in contas:
        if conta.status == StatusPagar.cancelado:
            continue
        valor = Decimal(conta.valor)
        saldo = valor - Decimal(conta.valor_pago or 0)
        if conta.status == StatusPagar.pago:
            pago += Decimal(conta.valor_pago or 0)
        else:
            aberto += saldo
            if conta.data_vencimento < hoje:
                vencido += saldo
            else:
                vencer += saldo
    return {"total_aberto": aberto, "total_pago": pago, "vencido": vencido, "a_vencer": vencer}


def resumo_receber(contas: list[ContaReceber]) -> dict[str, Decimal]:
    hoje = date.today()
    aberto = vencido = receber = recebido = Decimal("0")
    for conta in contas:
        if conta.status == StatusReceber.cancelado:
            continue
        valor = Decimal(conta.valor)
        saldo = valor - Decimal(conta.valor_recebido or 0)
        if conta.status == StatusReceber.recebido:
            recebido += Decimal(conta.valor_recebido or 0)
        else:
            aberto += saldo
            if conta.data_vencimento < hoje:
                vencido += saldo
            else:
                receber += saldo
    return {"total_aberto": aberto, "recebido": recebido, "vencido": vencido, "a_receber": receber}


def fluxo_realizado(db: Session, empresa_id: int, start: date | None = None, end: date | None = None):
    movimentos = []
    stmt = select(BaixaFinanceira).where(BaixaFinanceira.empresa_id == empresa_id)
    if start:
        stmt = stmt.where(BaixaFinanceira.data_baixa >= start)
    if end:
        stmt = stmt.where(BaixaFinanceira.data_baixa <= end)
    for baixa in db.scalars(stmt):
        conta = db.get(ContaReceber, baixa.conta_receber_id) if baixa.tipo == TipoBaixa.receber else db.get(ContaPagar, baixa.conta_pagar_id)
        if not conta:
            continue
        categoria = db.get(Categoria, conta.categoria_id)
        movimentos.append({
            "baixa_id": baixa.id,
            "conta_id": conta.id,
            "origem_tipo": baixa.tipo.value,
            "data": baixa.data_baixa,
            "descricao": conta.descricao,
            "categoria_id": conta.categoria_id,
            "subcategoria_id": conta.subcategoria_id,
            "categoria": categoria.nome if categoria else "-",
            "tipo": "Entrada" if baixa.tipo == TipoBaixa.receber else "Saída",
            "valor": Decimal(baixa.valor) if baixa.tipo == TipoBaixa.receber else -Decimal(baixa.valor),
        })
    saldo = Decimal("0")
    rows = []
    for mov in sorted(movimentos, key=lambda item: item["data"]):
        saldo += mov["valor"]
        rows.append({**mov, "saldo_acumulado": saldo})
    return rows

def fluxo_projetado(db: Session, empresa_id: int, dias: int = 90, saldo_inicial: Decimal = Decimal("0")):
    hoje = date.today()
    fim = hoje + timedelta(days=dias)
    movimentos = []
    for conta in db.scalars(select(ContaReceber).where(
        ContaReceber.empresa_id == empresa_id,
        ContaReceber.status.in_([StatusReceber.aberto, StatusReceber.parcial]),
        ContaReceber.data_vencimento.between(hoje, fim),
    )):
        saldo = Decimal(conta.valor) - Decimal(conta.valor_recebido or 0)
        movimentos.append({"data": conta.data_vencimento, "descricao": conta.descricao, "tipo": "Entrada prevista", "valor": saldo})
    for conta in db.scalars(select(ContaPagar).where(
        ContaPagar.empresa_id == empresa_id,
        ContaPagar.status.in_([StatusPagar.aberto, StatusPagar.parcial]),
        ContaPagar.data_vencimento.between(hoje, fim),
    )):
        saldo = Decimal(conta.valor) - Decimal(conta.valor_pago or 0)
        movimentos.append({"data": conta.data_vencimento, "descricao": conta.descricao, "tipo": "Saída prevista", "valor": -saldo})
    saldo = saldo_inicial
    rows = []
    for mov in sorted(movimentos, key=lambda item: item["data"]):
        saldo += mov["valor"]
        rows.append({**mov, "saldo_final_projetado": saldo})
    return rows


def dashboard(db: Session, empresa_id: int):
    hoje = date.today()
    pagar = db.scalars(select(ContaPagar).where(ContaPagar.empresa_id == empresa_id)).all()
    receber = db.scalars(select(ContaReceber).where(ContaReceber.empresa_id == empresa_id)).all()
    recebido_mes = sum((Decimal(c.valor_recebido or 0) for c in receber if c.data_vencimento.month == hoje.month and c.status == StatusReceber.recebido), Decimal("0"))
    pago_mes = sum((Decimal(c.valor_pago or 0) for c in pagar if c.data_vencimento.month == hoje.month and c.status == StatusPagar.pago), Decimal("0"))
    saldo_atual = sum((Decimal(c.valor_recebido or 0) for c in receber if c.status == StatusReceber.recebido), Decimal("0")) - sum((Decimal(c.valor_pago or 0) for c in pagar if c.status == StatusPagar.pago), Decimal("0"))
    contas_pagar_aberto = resumo_pagar(pagar)["total_aberto"]
    contas_receber_aberto = resumo_receber(receber)["total_aberto"]

    receitas_categoria = defaultdict(Decimal)
    despesas_categoria = defaultdict(Decimal)
    for conta in receber:
        if conta.status == StatusReceber.cancelado:
            continue
        categoria = db.get(Categoria, conta.categoria_id)
        receitas_categoria[categoria.nome if categoria else "Sem categoria"] += Decimal(conta.valor)
    for conta in pagar:
        if conta.status == StatusPagar.cancelado:
            continue
        categoria = db.get(Categoria, conta.categoria_id)
        despesas_categoria[categoria.nome if categoria else "Sem categoria"] += Decimal(conta.valor)

    resultado_mensal = []
    for mes in range(1, 13):
        receita = sum((Decimal(c.valor_recebido or 0) for c in receber if c.data_vencimento.month == mes), Decimal("0"))
        despesa = sum((Decimal(c.valor_pago or 0) for c in pagar if c.data_vencimento.month == mes), Decimal("0"))
        resultado_mensal.append({"mes": mes, "receitas": receita, "despesas": despesa, "resultado": receita - despesa})

    inadimplencia = sum((Decimal(c.valor) - Decimal(c.valor_recebido or 0) for c in receber if c.data_vencimento < hoje and c.status in [StatusReceber.aberto, StatusReceber.parcial]), Decimal("0"))
    return {
        "kpis": {
            "saldo_atual": saldo_atual,
            "receitas_mes": recebido_mes,
            "despesas_mes": pago_mes,
            "resultado_mes": recebido_mes - pago_mes,
            "resultado_acumulado": saldo_atual,
            "contas_pagar_aberto": contas_pagar_aberto,
            "contas_receber_aberto": contas_receber_aberto,
            "inadimplencia": inadimplencia,
        },
        "evolucao_caixa": fluxo_realizado(db, empresa_id),
        "receitas_por_categoria": [{"categoria": k, "valor": v} for k, v in receitas_categoria.items()],
        "despesas_por_categoria": [{"categoria": k, "valor": v} for k, v in despesas_categoria.items()],
        "resultado_mensal": resultado_mensal,
    }


def monthly_report(db: Session, empresa_id: int, year: int, month: int):
    pagar = db.scalars(select(ContaPagar).where(ContaPagar.empresa_id == empresa_id, extract("year", ContaPagar.data_vencimento) == year, extract("month", ContaPagar.data_vencimento) == month)).all()
    receber = db.scalars(select(ContaReceber).where(ContaReceber.empresa_id == empresa_id, extract("year", ContaReceber.data_vencimento) == year, extract("month", ContaReceber.data_vencimento) == month)).all()
    return {"ano": year, "mes": month, "receitas": resumo_receber(receber), "despesas": resumo_pagar(pagar)}


def annual_report(db: Session, empresa_id: int, year: int):
    return {"ano": year, "meses": [monthly_report(db, empresa_id, year, month) for month in range(1, 13)]}


import json
from datetime import date
from decimal import Decimal
from io import BytesIO

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import extract, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.entities import BaixaFinanceira, Categoria, ContaPagar, ContaReceber, Subcategoria
from app.models.enums import StatusPagar, StatusReceber, TipoBaixa
from app.routes.deps import get_empresa_id
from app.services.finance import annual_report, dashboard, fluxo_projetado, fluxo_realizado, monthly_report
from app.services.nfe import import_nfe_xml

router = APIRouter(tags=["Financeiro"])


class FluxoBaixaUpdate(BaseModel):
    data_baixa: date | None = None
    valor: Decimal | None = None
    categoria_id: int | None = None
    subcategoria_id: int | None = None


def atualizar_status_pagar(conta: ContaPagar) -> None:
    if conta.valor_pago <= 0:
        conta.status = StatusPagar.aberto
    elif conta.valor_pago >= conta.valor:
        conta.status = StatusPagar.pago
    else:
        conta.status = StatusPagar.parcial


def atualizar_status_receber(conta: ContaReceber) -> None:
    if conta.valor_recebido <= 0:
        conta.status = StatusReceber.aberto
    elif conta.valor_recebido >= conta.valor:
        conta.status = StatusReceber.recebido
    else:
        conta.status = StatusReceber.parcial


@router.get("/dashboard")
def get_dashboard(empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return dashboard(db, empresa_id)


@router.get("/fluxo-caixa")
def get_fluxo_caixa(start: date | None = None, end: date | None = None, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return fluxo_realizado(db, empresa_id, start, end)


@router.get("/fluxo-caixa/mensal")
def get_fluxo_mensal(year: int, month: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    first_day = date(year, month, 1)
    baixas_anteriores = db.scalars(select(BaixaFinanceira).where(
        BaixaFinanceira.empresa_id == empresa_id,
        BaixaFinanceira.data_baixa < first_day,
    )).all()
    saldo_inicial = sum(
        Decimal(baixa.valor) if baixa.tipo == TipoBaixa.receber else -Decimal(baixa.valor)
        for baixa in baixas_anteriores
    )
    categorias = db.scalars(select(Categoria).where(
        Categoria.empresa_id == empresa_id,
        Categoria.ativa.is_(True),
    ).options(selectinload(Categoria.subcategorias)).order_by(Categoria.tipo, Categoria.nome)).all()
    pagar = db.scalars(select(ContaPagar).where(
        ContaPagar.empresa_id == empresa_id,
        ContaPagar.status != StatusPagar.cancelado,
        extract("year", ContaPagar.data_vencimento) == year,
        extract("month", ContaPagar.data_vencimento) == month,
    )).all()
    receber = db.scalars(select(ContaReceber).where(
        ContaReceber.empresa_id == empresa_id,
        ContaReceber.status != StatusReceber.cancelado,
        extract("year", ContaReceber.data_vencimento) == year,
        extract("month", ContaReceber.data_vencimento) == month,
    )).all()
    categoria_nome = {categoria.id: categoria.nome for categoria in categorias}
    subcategoria_nome = {
        sub.id: sub.nome
        for categoria in categorias
        for sub in categoria.subcategorias
    }

    valores = {}
    for conta in receber:
        key = (conta.categoria_id, conta.subcategoria_id)
        valores[key] = valores.get(key, Decimal("0")) + Decimal(conta.valor)
    for conta in pagar:
        key = (conta.categoria_id, conta.subcategoria_id)
        valores[key] = valores.get(key, Decimal("0")) - Decimal(conta.valor)

    resumo = []
    for categoria in categorias:
        for sub in sorted((item for item in categoria.subcategorias if item.ativa), key=lambda item: item.nome):
            resumo.append({
                "categoria_id": categoria.id,
                "subcategoria_id": sub.id,
                "tipo_categoria": categoria.tipo.value,
                "categoria": categoria.nome,
                "subcategoria": sub.nome,
                "valor": valores.get((categoria.id, sub.id), Decimal("0")),
            })

    lancamentos = []
    for conta in receber:
        recebido = Decimal(conta.valor_recebido or 0)
        saldo = Decimal(conta.valor) - recebido
        lancamentos.append({
            "id": conta.id,
            "tipo": "Entrada",
            "data": conta.data_vencimento,
            "descricao": conta.descricao,
            "categoria_id": conta.categoria_id,
            "subcategoria_id": conta.subcategoria_id,
            "categoria": categoria_nome.get(conta.categoria_id, "-"),
            "subcategoria": subcategoria_nome.get(conta.subcategoria_id, "-"),
            "valor": Decimal(conta.valor),
            "baixado": recebido,
            "saldo": saldo,
            "status_fluxo": "Baixado" if saldo <= 0 else ("Parcial" if recebido > 0 else "Em aberto"),
            "status": conta.status.value,
        })
    for conta in pagar:
        pago = Decimal(conta.valor_pago or 0)
        saldo = Decimal(conta.valor) - pago
        lancamentos.append({
            "id": conta.id,
            "tipo": "Saída",
            "data": conta.data_vencimento,
            "descricao": conta.descricao,
            "categoria_id": conta.categoria_id,
            "subcategoria_id": conta.subcategoria_id,
            "categoria": categoria_nome.get(conta.categoria_id, "-"),
            "subcategoria": subcategoria_nome.get(conta.subcategoria_id, "-"),
            "valor": -Decimal(conta.valor),
            "baixado": -pago,
            "saldo": -saldo,
            "status_fluxo": "Baixado" if saldo <= 0 else ("Parcial" if pago > 0 else "Em aberto"),
            "status": conta.status.value,
        })
    lancamentos.sort(key=lambda item: (item["data"], item["descricao"]))
    return {"saldo_inicial": saldo_inicial, "resumo": resumo, "lancamentos": lancamentos}


@router.get("/fluxo-caixa/projetado-anual")
def get_fluxo_projetado_anual(year: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    first_day = date(year, 1, 1)
    baixas_anteriores = db.scalars(select(BaixaFinanceira).where(
        BaixaFinanceira.empresa_id == empresa_id,
        BaixaFinanceira.data_baixa < first_day,
    )).all()
    saldo_inicial = sum(
        Decimal(baixa.valor) if baixa.tipo == TipoBaixa.receber else -Decimal(baixa.valor)
        for baixa in baixas_anteriores
    )
    categorias = db.scalars(select(Categoria).where(
        Categoria.empresa_id == empresa_id,
        Categoria.ativa.is_(True),
    ).options(selectinload(Categoria.subcategorias)).order_by(Categoria.tipo, Categoria.nome)).all()
    pagar = db.scalars(select(ContaPagar).where(
        ContaPagar.empresa_id == empresa_id,
        ContaPagar.status != StatusPagar.cancelado,
        extract("year", ContaPagar.data_vencimento) == year,
    )).all()
    receber = db.scalars(select(ContaReceber).where(
        ContaReceber.empresa_id == empresa_id,
        ContaReceber.status != StatusReceber.cancelado,
        extract("year", ContaReceber.data_vencimento) == year,
    )).all()

    valores = {}
    for conta in receber:
        key = (conta.categoria_id, conta.subcategoria_id)
        meses = valores.setdefault(key, [Decimal("0")] * 12)
        meses[conta.data_vencimento.month - 1] += Decimal(conta.valor)
    for conta in pagar:
        key = (conta.categoria_id, conta.subcategoria_id)
        meses = valores.setdefault(key, [Decimal("0")] * 12)
        meses[conta.data_vencimento.month - 1] -= Decimal(conta.valor)

    resumo = []
    for categoria in categorias:
        for sub in sorted((item for item in categoria.subcategorias if item.ativa), key=lambda item: item.nome):
            meses = valores.get((categoria.id, sub.id), [Decimal("0")] * 12)
            resumo.append({
                "categoria_id": categoria.id,
                "subcategoria_id": sub.id,
                "tipo_categoria": categoria.tipo.value,
                "categoria": categoria.nome,
                "subcategoria": sub.nome,
                "meses": meses,
                "total": sum(meses),
            })

    entradas = [Decimal("0")] * 12
    saidas = [Decimal("0")] * 12
    for row in resumo:
        target = entradas if str(row["tipo_categoria"]).lower().startswith("receita") else saidas
        for idx, value in enumerate(row["meses"]):
            target[idx] += value
    liquido = [entradas[idx] + saidas[idx] for idx in range(12)]
    saldos = []
    saldo_atual = saldo_inicial
    for value in liquido:
        saldo_atual += value
        saldos.append(saldo_atual)

    return {
        "year": year,
        "saldo_inicial": saldo_inicial,
        "resumo": resumo,
        "totais": {
            "entradas": entradas,
            "saidas": saidas,
            "liquido": liquido,
            "saldo_final": saldos,
        },
    }


@router.put("/fluxo-caixa/baixas/{baixa_id}")
def update_fluxo_baixa(baixa_id: int, payload: FluxoBaixaUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    baixa = db.scalar(select(BaixaFinanceira).where(BaixaFinanceira.id == baixa_id, BaixaFinanceira.empresa_id == empresa_id))
    if not baixa:
        raise HTTPException(404, "Baixa não encontrada.")

    conta = db.get(ContaReceber, baixa.conta_receber_id) if baixa.tipo == TipoBaixa.receber else db.get(ContaPagar, baixa.conta_pagar_id)
    if not conta or conta.empresa_id != empresa_id:
        raise HTTPException(404, "Lançamento de origem não encontrado.")

    if payload.data_baixa is not None:
        baixa.data_baixa = payload.data_baixa

    if payload.valor is not None:
        if payload.valor <= 0:
            raise HTTPException(400, "Valor deve ser maior que zero.")
        delta = payload.valor - baixa.valor
        if baixa.tipo == TipoBaixa.receber:
            novo_total = conta.valor_recebido + delta
            if novo_total < 0 or novo_total > conta.valor:
                raise HTTPException(400, "Valor recebido não pode ultrapassar o valor do lançamento.")
            conta.valor_recebido = novo_total
            atualizar_status_receber(conta)
        else:
            novo_total = conta.valor_pago + delta
            if novo_total < 0 or novo_total > conta.valor:
                raise HTTPException(400, "Valor pago não pode ultrapassar o valor do lançamento.")
            conta.valor_pago = novo_total
            atualizar_status_pagar(conta)
        baixa.valor = payload.valor

    if payload.categoria_id is not None:
        categoria = db.scalar(select(Categoria).where(Categoria.id == payload.categoria_id, Categoria.empresa_id == empresa_id))
        if not categoria:
            raise HTTPException(404, "Categoria não encontrada.")
        conta.categoria_id = categoria.id

    if payload.subcategoria_id is not None:
        subcategoria = db.scalar(select(Subcategoria).join(Categoria).where(
            Subcategoria.id == payload.subcategoria_id,
            Categoria.empresa_id == empresa_id,
            Subcategoria.categoria_id == conta.categoria_id,
        ))
        if not subcategoria:
            raise HTTPException(404, "Subcategoria não encontrada para a categoria selecionada.")
        conta.subcategoria_id = subcategoria.id

    db.commit()
    return {"ok": True}


@router.get("/fluxo-caixa-projetado")
def get_fluxo_projetado(dias: int = 90, saldo_inicial: Decimal = Decimal("0"), empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return fluxo_projetado(db, empresa_id, dias, saldo_inicial)


@router.post("/xml-nfe/importar")
async def importar_xml_nfe(file: UploadFile, item_categories: str | None = Form(None), empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    content = (await file.read()).decode("utf-8", errors="ignore")
    assignments = json.loads(item_categories) if item_categories else None
    nota, contas = import_nfe_xml(db, empresa_id, content, assignments)
    return {
        "id": nota.id,
        "numero_nf": nota.numero_nf,
        "valor_total": nota.valor_total,
        "fornecedor_id": nota.fornecedor_id,
        "fornecedor_nome": nota.nome_emitente,
        "contas_pagar_ids": [conta.id for conta in contas],
        "parcelas": len(contas),
    }


@router.get("/relatorios/mensal")
def relatorio_mensal(year: int, month: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return monthly_report(db, empresa_id, year, month)


@router.get("/relatorios/anual")
def relatorio_anual(year: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return annual_report(db, empresa_id, year)


@router.get("/relatorios/mensal.xlsx")
def export_mensal_xlsx(year: int, month: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    data = monthly_report(db, empresa_id, year, month)
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório Mensal"
    ws.append(["Indicador", "Valor"])
    for group in ["receitas", "despesas"]:
        ws.append([group.upper(), ""])
        for key, value in data[group].items():
            ws.append([key, float(value)])
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"relatorio-mensal-{year}-{month:02d}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/relatorios/mensal.pdf")
def export_mensal_pdf(year: int, month: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    data = monthly_report(db, empresa_id, year, month)
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setTitle("Relatório Mensal")
    y = 800
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, y, f"Relatório mensal {month:02d}/{year}")
    y -= 30
    pdf.setFont("Helvetica", 10)
    for group in ["receitas", "despesas"]:
        pdf.drawString(40, y, group.upper())
        y -= 18
        for key, value in data[group].items():
            pdf.drawString(60, y, f"{key}: R$ {float(value):,.2f}")
            y -= 16
    pdf.save()
    output.seek(0)
    return StreamingResponse(output, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=relatorio-mensal.pdf"})

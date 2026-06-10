from datetime import date
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.entities import BaixaFinanceira, Categoria, Cliente, ContaPagar, ContaReceber, Fornecedor, HistoricoAlteracao, NotaFiscalXML, ProdutoNota, Subcategoria
from app.models.enums import TipoBaixa
from app.models.enums import StatusCadastro, StatusPagar, StatusReceber, TipoCategoria
from app.repositories.base import create_entity, get_entity, list_entities, update_entity
from app.routes.deps import get_empresa_id
from app.schemas.common import (
    BaixaPayload,
    ContaPagarDetail,
    CategoriaCreate,
    CategoriaOut,
    ContaPagarListItem,
    CategoriaUpdate,
    ContaPagarCreate,
    ContaPagarOut,
    ContaPagarUpdate,
    ContaReceberCreate,
    ContaReceberOut,
    ContaReceberUpdate,
    NotaFiscalOut,
    PessoaCreate,
    PessoaOut,
    PessoaUpdate,
    SubcategoriaCreate,
    SubcategoriaOut,
    SubcategoriaUpdate,
)
from app.services.finance import baixar_conta_pagar, baixar_conta_receber, resumo_pagar, resumo_receber
from app.services.nfe import rebuild_contas_pagar_from_nota

router = APIRouter(tags=["CRUD"])


class ProdutoCategoriaPayload(BaseModel):
    produto_id: int
    categoria_id: int
    subcategoria_id: int


class NotaCategoriasPayload(BaseModel):
    produtos: list[ProdutoCategoriaPayload]


def not_found(entity):
    if not entity:
        raise HTTPException(404, "Registro não encontrado.")
    return entity


def registrar_historico(db: Session, empresa_id: int, entidade: str, entidade_id: int, acao: str, dados: str | None = None):
    db.add(HistoricoAlteracao(
        empresa_id=empresa_id,
        entidade=entidade,
        entidade_id=entidade_id,
        acao=acao,
        dados=dados,
    ))


def sync_xml_fields_from_description(db: Session, conta: ContaPagar, empresa_id: int) -> bool:
    changed = False
    nf_match = re.search(r"NF-e\s+([A-Za-z0-9.-]+)", conta.descricao or "", flags=re.IGNORECASE)
    if nf_match and not conta.nota_fiscal_id:
        nota = db.scalar(select(NotaFiscalXML).where(
            NotaFiscalXML.empresa_id == empresa_id,
            NotaFiscalXML.numero_nf == nf_match.group(1),
        ))
        if nota:
            conta.nota_fiscal_id = nota.id
            conta.nota_fiscal = nota
            changed = True
            if nota.fornecedor_id and not conta.fornecedor_id:
                conta.fornecedor_id = nota.fornecedor_id
                conta.fornecedor = db.get(Fornecedor, nota.fornecedor_id)
    elif conta.nota_fiscal_id and not getattr(conta, "nota_fiscal", None):
        conta.nota_fiscal = db.get(NotaFiscalXML, conta.nota_fiscal_id)

    if conta.nota_fiscal and conta.nota_fiscal.fornecedor_id and not conta.fornecedor_id:
        conta.fornecedor_id = conta.nota_fiscal.fornecedor_id
        conta.fornecedor = db.get(Fornecedor, conta.nota_fiscal.fornecedor_id)
        changed = True

    boleto_match = re.search(r"Boleto\s+([A-Za-z0-9.-]+)", conta.descricao or "", flags=re.IGNORECASE)
    if boleto_match and not conta.numero_boleto:
        conta.numero_boleto = boleto_match.group(1)
        changed = True

    parcela_match = re.search(r"Parcela\s+(\d+)\s*/\s*(\d+)", conta.descricao or "", flags=re.IGNORECASE)
    if parcela_match:
        if not conta.numero_parcela:
            conta.numero_parcela = int(parcela_match.group(1))
            changed = True
        if not conta.total_parcelas:
            conta.total_parcelas = int(parcela_match.group(2))
            changed = True
    return changed


def cliente_from_nota(db: Session, nota: NotaFiscalXML | None, empresa_id: int) -> Cliente | None:
    if not nota:
        return None
    cliente = None
    if nota.cnpj_emitente:
        cliente = db.scalar(select(Cliente).where(
            Cliente.empresa_id == empresa_id,
            Cliente.cpf_cnpj == nota.cnpj_emitente,
        ))
    if not cliente and nota.nome_emitente:
        cliente = db.scalar(select(Cliente).where(
            Cliente.empresa_id == empresa_id,
            Cliente.nome_razao_social == nota.nome_emitente,
        ))
    return cliente


def ensure_cliente_from_nota(db: Session, nota: NotaFiscalXML, empresa_id: int) -> Cliente:
    cliente = cliente_from_nota(db, nota, empresa_id)
    if cliente:
        return cliente
    cliente = Cliente(
        empresa_id=empresa_id,
        nome_razao_social=nota.nome_emitente or f"Cliente NF-e {nota.numero_nf or nota.id}",
        cpf_cnpj=nota.cnpj_emitente,
    )
    db.add(cliente)
    db.flush()
    registrar_historico(
        db,
        empresa_id,
        "clientes",
        cliente.id,
        "criado_por_nfe",
        f"Cliente criado automaticamente pela NF-e {nota.numero_nf or nota.chave_acesso or nota.id}.",
    )
    return cliente


def conta_pagar_item(conta: ContaPagar, cliente_nf: Cliente | None = None) -> dict:
    nota = getattr(conta, "nota_fiscal", None)
    fornecedor_nome = conta.fornecedor.nome_razao_social if conta.fornecedor else None
    if not fornecedor_nome and nota:
        fornecedor_nome = nota.nome_emitente
    return {
        **ContaPagarOut.model_validate(conta).model_dump(),
        "fornecedor_nome": fornecedor_nome,
        "categoria_nome": conta.categoria.nome if conta.categoria else None,
        "subcategoria_nome": conta.subcategoria.nome if conta.subcategoria else None,
        "numero_nf": nota.numero_nf if nota else None,
        "cnpj_emitente": nota.cnpj_emitente if nota else None,
        "nome_emitente": nota.nome_emitente if nota else None,
        "cliente_nf_id": cliente_nf.id if cliente_nf else None,
        "cliente_nf_nome": cliente_nf.nome_razao_social if cliente_nf else None,
        "saldo_restante": conta.valor - (conta.valor_pago or 0),
    }


@router.get("/clientes", response_model=list[PessoaOut])
def list_clientes(q: str | None = None, status: StatusCadastro | None = None, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    stmt = select(Cliente).where(Cliente.empresa_id == empresa_id)
    if q:
        stmt = stmt.where(Cliente.nome_razao_social.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(Cliente.status == status)
    return db.scalars(stmt).all()


@router.post("/clientes", response_model=PessoaOut)
def create_cliente(payload: PessoaCreate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return create_entity(db, Cliente, empresa_id, payload.model_dump(exclude_none=True))


@router.put("/clientes/{entity_id}", response_model=PessoaOut)
def update_cliente(entity_id: int, payload: PessoaUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return update_entity(db, not_found(get_entity(db, Cliente, entity_id, empresa_id)), payload.model_dump(exclude_unset=True))


@router.delete("/clientes/{entity_id}")
def delete_cliente(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    entity = not_found(get_entity(db, Cliente, entity_id, empresa_id))
    db.delete(entity)
    db.commit()
    return {"ok": True}


@router.get("/fornecedores", response_model=list[PessoaOut])
def list_fornecedores(q: str | None = None, status: StatusCadastro | None = None, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    stmt = select(Fornecedor).where(Fornecedor.empresa_id == empresa_id)
    if q:
        stmt = stmt.where(Fornecedor.nome_razao_social.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(Fornecedor.status == status)
    return db.scalars(stmt).all()


@router.post("/fornecedores", response_model=PessoaOut)
def create_fornecedor(payload: PessoaCreate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return create_entity(db, Fornecedor, empresa_id, payload.model_dump(exclude_none=True))


@router.put("/fornecedores/{entity_id}", response_model=PessoaOut)
def update_fornecedor(entity_id: int, payload: PessoaUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return update_entity(db, not_found(get_entity(db, Fornecedor, entity_id, empresa_id)), payload.model_dump(exclude_unset=True))


@router.delete("/fornecedores/{entity_id}")
def delete_fornecedor(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    entity = not_found(get_entity(db, Fornecedor, entity_id, empresa_id))
    db.delete(entity)
    db.commit()
    return {"ok": True}


@router.get("/categorias", response_model=list[CategoriaOut])
def list_categorias(empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return db.scalars(select(Categoria).where(Categoria.empresa_id == empresa_id).options(selectinload(Categoria.subcategorias)).order_by(Categoria.tipo, Categoria.nome)).all()


@router.post("/categorias", response_model=CategoriaOut)
def create_categoria(payload: CategoriaCreate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return create_entity(db, Categoria, empresa_id, payload.model_dump())


@router.put("/categorias/{entity_id}", response_model=CategoriaOut)
def update_categoria(entity_id: int, payload: CategoriaUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return update_entity(db, not_found(get_entity(db, Categoria, entity_id, empresa_id)), payload.model_dump(exclude_unset=True))


@router.delete("/categorias/{entity_id}")
def delete_categoria(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    if db.scalar(select(ContaPagar.id).where(ContaPagar.categoria_id == entity_id)) or db.scalar(select(ContaReceber.id).where(ContaReceber.categoria_id == entity_id)):
        raise HTTPException(400, "Categoria possui lançamentos vinculados.")
    entity = not_found(get_entity(db, Categoria, entity_id, empresa_id))
    db.delete(entity)
    db.commit()
    return {"ok": True}


@router.post("/subcategorias", response_model=SubcategoriaOut)
def create_subcategoria(payload: SubcategoriaCreate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    categoria = not_found(get_entity(db, Categoria, payload.categoria_id, empresa_id))
    sub = Subcategoria(categoria_id=categoria.id, nome=payload.nome, ativa=payload.ativa)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.put("/subcategorias/{entity_id}", response_model=SubcategoriaOut)
def update_subcategoria(entity_id: int, payload: SubcategoriaUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subcategoria).join(Categoria).where(Subcategoria.id == entity_id, Categoria.empresa_id == empresa_id))
    sub = not_found(sub)
    return update_entity(db, sub, payload.model_dump(exclude_unset=True))


@router.delete("/subcategorias/{entity_id}")
def delete_subcategoria(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subcategoria).join(Categoria).where(Subcategoria.id == entity_id, Categoria.empresa_id == empresa_id))
    sub = not_found(sub)
    if db.scalar(select(ContaPagar.id).where(ContaPagar.subcategoria_id == entity_id)) or db.scalar(select(ContaReceber.id).where(ContaReceber.subcategoria_id == entity_id)):
        raise HTTPException(400, "Subcategoria possui lançamentos vinculados.")
    db.delete(sub)
    db.commit()
    return {"ok": True}


@router.get("/contas-pagar")
def list_contas_pagar(
    status: StatusPagar | None = None,
    fornecedor_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    empresa_id: int = Depends(get_empresa_id),
    db: Session = Depends(get_db),
):
    stmt = select(ContaPagar).where(ContaPagar.empresa_id == empresa_id).options(
        selectinload(ContaPagar.fornecedor),
        selectinload(ContaPagar.categoria),
        selectinload(ContaPagar.subcategoria),
        selectinload(ContaPagar.nota_fiscal),
    )
    if status:
        stmt = stmt.where(ContaPagar.status == status)
    else:
        stmt = stmt.where(ContaPagar.status.in_([StatusPagar.aberto, StatusPagar.parcial]))
    if fornecedor_id:
        stmt = stmt.where(ContaPagar.fornecedor_id == fornecedor_id)
    if start:
        stmt = stmt.where(ContaPagar.data_vencimento >= start)
    if end:
        stmt = stmt.where(ContaPagar.data_vencimento <= end)
    contas = db.scalars(stmt.order_by(ContaPagar.data_vencimento)).all()
    changed = False
    for conta in contas:
        changed = sync_xml_fields_from_description(db, conta, empresa_id) or changed
    if changed:
        db.commit()
    items = []
    for conta in contas:
        items.append(ContaPagarListItem.model_validate(conta_pagar_item(conta, cliente_from_nota(db, conta.nota_fiscal, empresa_id))))
    return {"items": items, "summary": resumo_pagar(contas)}


@router.get("/contas-pagar/{entity_id}", response_model=ContaPagarDetail)
def get_conta_pagar(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    conta = db.scalar(select(ContaPagar).where(
        ContaPagar.id == entity_id,
        ContaPagar.empresa_id == empresa_id,
    ).options(
        selectinload(ContaPagar.fornecedor),
        selectinload(ContaPagar.categoria),
        selectinload(ContaPagar.subcategoria),
        selectinload(ContaPagar.nota_fiscal),
    ))
    conta = not_found(conta)
    if sync_xml_fields_from_description(db, conta, empresa_id):
        db.commit()
        db.refresh(conta)
    baixas = db.scalars(select(BaixaFinanceira).where(
        BaixaFinanceira.empresa_id == empresa_id,
        BaixaFinanceira.tipo == TipoBaixa.pagar,
        BaixaFinanceira.conta_pagar_id == entity_id,
    ).order_by(BaixaFinanceira.data_baixa, BaixaFinanceira.id)).all()
    historico = db.scalars(select(HistoricoAlteracao).where(
        HistoricoAlteracao.empresa_id == empresa_id,
        HistoricoAlteracao.entidade == "contas_pagar",
        HistoricoAlteracao.entidade_id == entity_id,
    ).order_by(HistoricoAlteracao.created_at, HistoricoAlteracao.id)).all()
    return ContaPagarDetail.model_validate({
        **conta_pagar_item(conta, cliente_from_nota(db, conta.nota_fiscal, empresa_id)),
        "baixas": baixas,
        "historico": historico,
    })


@router.post("/contas-pagar", response_model=ContaPagarOut)
def create_conta_pagar(payload: ContaPagarCreate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    conta = ContaPagar(empresa_id=empresa_id, **payload.model_dump())
    db.add(conta)
    db.flush()
    registrar_historico(db, empresa_id, "contas_pagar", conta.id, "criado", f"Lançamento criado no valor de {conta.valor}.")
    db.commit()
    db.refresh(conta)
    return conta


@router.put("/contas-pagar/{entity_id}", response_model=ContaPagarOut)
def update_conta_pagar(entity_id: int, payload: ContaPagarUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    conta = not_found(get_entity(db, ContaPagar, entity_id, empresa_id))
    data = payload.model_dump(exclude_unset=True)
    changed = []
    for key, value in data.items():
        before = getattr(conta, key)
        if value != before:
            changed.append(f"{key}: {before} -> {value}")
            setattr(conta, key, value)
    if changed:
        registrar_historico(db, empresa_id, "contas_pagar", conta.id, "editado", "; ".join(changed))
    db.commit()
    db.refresh(conta)
    return conta


@router.post("/contas-pagar/{entity_id}/baixas", response_model=ContaPagarOut)
def baixa_pagar(entity_id: int, payload: BaixaPayload, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    conta = baixar_conta_pagar(db, not_found(get_entity(db, ContaPagar, entity_id, empresa_id)), payload)
    registrar_historico(db, empresa_id, "contas_pagar", conta.id, "baixa", f"Baixa de {payload.valor} em {payload.data_baixa}.")
    db.commit()
    db.refresh(conta)
    return conta


@router.delete("/contas-pagar/{entity_id}")
def delete_conta_pagar(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    entity = not_found(get_entity(db, ContaPagar, entity_id, empresa_id))
    registrar_historico(db, empresa_id, "contas_pagar", entity.id, "excluido", f"Lançamento excluído: {entity.descricao}.")
    db.delete(entity)
    db.commit()
    return {"ok": True}


@router.get("/contas-receber")
def list_contas_receber(status: StatusReceber | None = None, cliente_id: int | None = None, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    filters = {"status": status, "cliente_id": cliente_id}
    contas = list_entities(db, ContaReceber, empresa_id, filters)
    return {"items": [ContaReceberOut.model_validate(c) for c in contas], "summary": resumo_receber(contas)}


@router.post("/contas-receber", response_model=ContaReceberOut)
def create_conta_receber(payload: ContaReceberCreate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return create_entity(db, ContaReceber, empresa_id, payload.model_dump())


@router.put("/contas-receber/{entity_id}", response_model=ContaReceberOut)
def update_conta_receber(entity_id: int, payload: ContaReceberUpdate, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return update_entity(db, not_found(get_entity(db, ContaReceber, entity_id, empresa_id)), payload.model_dump(exclude_unset=True))


@router.post("/contas-receber/{entity_id}/baixas", response_model=ContaReceberOut)
def baixa_receber(entity_id: int, payload: BaixaPayload, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return baixar_conta_receber(db, not_found(get_entity(db, ContaReceber, entity_id, empresa_id)), payload)


@router.delete("/contas-receber/{entity_id}")
def delete_conta_receber(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    entity = not_found(get_entity(db, ContaReceber, entity_id, empresa_id))
    db.delete(entity)
    db.commit()
    return {"ok": True}


@router.get("/notas-fiscais", response_model=list[NotaFiscalOut])
def list_notas(empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    return db.scalars(select(NotaFiscalXML).where(NotaFiscalXML.empresa_id == empresa_id).options(selectinload(NotaFiscalXML.produtos))).all()


@router.delete("/notas-fiscais/{entity_id}")
def delete_nota_fiscal(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    nota = not_found(db.scalar(select(NotaFiscalXML).where(
        NotaFiscalXML.id == entity_id,
        NotaFiscalXML.empresa_id == empresa_id,
    ).options(selectinload(NotaFiscalXML.produtos))))
    contas = db.scalars(select(ContaPagar).where(
        ContaPagar.empresa_id == empresa_id,
        ContaPagar.nota_fiscal_id == entity_id,
    )).all()
    if any(conta.valor_pago and conta.valor_pago > 0 for conta in contas):
        raise HTTPException(400, "Nota fiscal possui conta a pagar com baixa registrada.")
    for conta in contas:
        baixas = db.scalars(select(BaixaFinanceira).where(
            BaixaFinanceira.empresa_id == empresa_id,
            BaixaFinanceira.tipo == TipoBaixa.pagar,
            BaixaFinanceira.conta_pagar_id == conta.id,
        )).all()
        for baixa in baixas:
            db.delete(baixa)
        historicos = db.scalars(select(HistoricoAlteracao).where(
            HistoricoAlteracao.empresa_id == empresa_id,
            HistoricoAlteracao.entidade == "contas_pagar",
            HistoricoAlteracao.entidade_id == conta.id,
        )).all()
        for historico in historicos:
            db.delete(historico)
        db.delete(conta)
    db.delete(nota)
    db.commit()
    return {"ok": True}


@router.put("/notas-fiscais/{entity_id}/categorias")
def update_nota_categorias(entity_id: int, payload: NotaCategoriasPayload, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    nota = not_found(db.scalar(select(NotaFiscalXML).where(
        NotaFiscalXML.id == entity_id,
        NotaFiscalXML.empresa_id == empresa_id,
    ).options(selectinload(NotaFiscalXML.produtos))))
    produtos_por_id = {produto.id: produto for produto in nota.produtos}
    for item in payload.produtos:
        produto = produtos_por_id.get(item.produto_id)
        if not produto:
            raise HTTPException(404, "Produto não encontrado na NF.")
        categoria = db.scalar(select(Categoria).where(
            Categoria.id == item.categoria_id,
            Categoria.empresa_id == empresa_id,
            Categoria.tipo == TipoCategoria.despesa,
        ))
        subcategoria = db.scalar(select(Subcategoria).where(
            Subcategoria.id == item.subcategoria_id,
            Subcategoria.categoria_id == item.categoria_id,
        ))
        if not categoria or not subcategoria:
            raise HTTPException(400, "Categoria ou subcategoria inválida para o produto.")
        produto.categoria_id = categoria.id
        produto.subcategoria_id = subcategoria.id
    contas = rebuild_contas_pagar_from_nota(db, nota, empresa_id)
    for conta in contas:
        registrar_historico(db, empresa_id, "contas_pagar", conta.id, "recalculado_xml", f"Lançamento recalculado pela categoria dos produtos da NF {nota.numero_nf or nota.id}.")
    db.commit()
    return {"ok": True, "contas_pagar_ids": [conta.id for conta in contas]}


@router.post("/notas-fiscais/{entity_id}/cliente", response_model=PessoaOut)
def create_cliente_from_nota(entity_id: int, empresa_id: int = Depends(get_empresa_id), db: Session = Depends(get_db)):
    nota = not_found(db.scalar(select(NotaFiscalXML).where(
        NotaFiscalXML.id == entity_id,
        NotaFiscalXML.empresa_id == empresa_id,
    )))
    cliente = ensure_cliente_from_nota(db, nota, empresa_id)
    db.commit()
    db.refresh(cliente)
    return cliente

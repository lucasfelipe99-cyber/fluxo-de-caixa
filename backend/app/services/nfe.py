from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Categoria, Cliente, ContaPagar, Fornecedor, HistoricoAlteracao, NotaFiscalXML, ProdutoNota, Subcategoria
from app.models.enums import OrigemLancamento, TipoCategoria


def _text(node, path: str, ns: dict[str, str]) -> str | None:
    found = node.find(path, ns)
    return found.text.strip() if found is not None and found.text else None


def _decimal(value: str | None) -> Decimal:
    return Decimal(value or "0")


def _children_by_local_name(node, name: str):
    return [child for child in list(node) if child.tag.split("}")[-1] == name]


def _descendants_by_local_name(node, name: str):
    return [child for child in node.iter() if child.tag.split("}")[-1] == name]


def get_default_despesa_category(db: Session, empresa_id: int) -> tuple[Categoria, Subcategoria]:
    categoria = db.scalar(select(Categoria).where(
        Categoria.empresa_id == empresa_id,
        Categoria.tipo == TipoCategoria.despesa,
        Categoria.nome == "Compras",
    ))
    if not categoria:
        categoria = Categoria(empresa_id=empresa_id, nome="Compras", tipo=TipoCategoria.despesa)
        db.add(categoria)
        db.flush()
    sub = db.scalar(select(Subcategoria).where(Subcategoria.categoria_id == categoria.id, Subcategoria.nome == "Produtos"))
    if not sub:
        sub = Subcategoria(categoria_id=categoria.id, nome="Produtos")
        db.add(sub)
        db.flush()
    return categoria, sub


def ensure_cliente_from_nfe(db: Session, empresa_id: int, cnpj: str | None, nome: str | None, numero: str | None) -> Cliente:
    cliente = None
    if cnpj:
        cliente = db.scalar(select(Cliente).where(Cliente.empresa_id == empresa_id, Cliente.cpf_cnpj == cnpj))
    if not cliente and nome:
        cliente = db.scalar(select(Cliente).where(Cliente.empresa_id == empresa_id, Cliente.nome_razao_social == nome))
    if cliente:
        return cliente
    cliente = Cliente(
        empresa_id=empresa_id,
        nome_razao_social=nome or f"Cliente NF-e {numero or 'sem numero'}",
        cpf_cnpj=cnpj,
    )
    db.add(cliente)
    db.flush()
    return cliente


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _duplicatas(root, ns: dict[str, str]) -> list[dict]:
    dup_nodes = root.findall(".//nfe:cobr/nfe:dup", ns) or root.findall(".//cobr/dup")
    parcelas = []
    for index, dup in enumerate(dup_nodes, start=1):
        numero = _text(dup, "nfe:nDup", ns) or _text(dup, "nDup", {}) or str(index)
        vencimento = _parse_date(_text(dup, "nfe:dVenc", ns) or _text(dup, "dVenc", {}))
        valor = _decimal(_text(dup, "nfe:vDup", ns) or _text(dup, "vDup", {}))
        parcelas.append({"numero_boleto": numero, "vencimento": vencimento, "valor": valor})
    return parcelas


def parcelas_from_nota(nota: NotaFiscalXML) -> list[dict]:
    try:
        root = ET.fromstring(nota.arquivo_xml)
    except ET.ParseError:
        root = None
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    parcelas = _duplicatas(root, ns) if root is not None else []
    vencimento = nota.data_emissao.date() if nota.data_emissao else date.today()
    if not parcelas:
        parcelas = [{"numero_boleto": None, "vencimento": vencimento, "valor": nota.valor_total}]
    return parcelas


def get_selected_category(db: Session, empresa_id: int, categoria_id: int | None, subcategoria_id: int | None) -> tuple[Categoria, Subcategoria]:
    if categoria_id and subcategoria_id:
        categoria = db.scalar(select(Categoria).where(
            Categoria.id == categoria_id,
            Categoria.empresa_id == empresa_id,
            Categoria.tipo == TipoCategoria.despesa,
        ))
        sub = db.scalar(select(Subcategoria).where(
            Subcategoria.id == subcategoria_id,
            Subcategoria.categoria_id == categoria_id,
        ))
        if categoria and sub:
            return categoria, sub
    return get_default_despesa_category(db, empresa_id)


def rebuild_contas_pagar_from_nota(db: Session, nota: NotaFiscalXML, empresa_id: int) -> list[ContaPagar]:
    contas_existentes = db.scalars(select(ContaPagar).where(
        ContaPagar.empresa_id == empresa_id,
        ContaPagar.nota_fiscal_id == nota.id,
    )).all()
    if any(conta.valor_pago and conta.valor_pago > 0 for conta in contas_existentes):
        raise HTTPException(400, "NF possui conta a pagar com baixa; não é possível recalcular categorias.")
    for conta in contas_existentes:
        db.delete(conta)
    db.flush()

    fornecedor_id = nota.fornecedor_id
    grupos = {}
    for produto in nota.produtos:
        categoria, sub = get_selected_category(db, empresa_id, produto.categoria_id, produto.subcategoria_id)
        produto.categoria_id = categoria.id
        produto.subcategoria_id = sub.id
        key = (categoria.id, sub.id)
        grupos[key] = grupos.get(key, Decimal("0")) + Decimal(produto.valor_total or 0)
    if not grupos:
        categoria, sub = get_default_despesa_category(db, empresa_id)
        grupos[(categoria.id, sub.id)] = nota.valor_total

    base_produtos = sum(grupos.values(), Decimal("0")) or nota.valor_total or Decimal("1")
    vencimento = nota.data_emissao.date() if nota.data_emissao else date.today()
    parcelas = parcelas_from_nota(nota)
    contas = []
    total_parcelas = len(parcelas)
    for index, parcela in enumerate(parcelas, start=1):
        parcela_label = f" Parcela {index}/{total_parcelas}" if total_parcelas > 1 else ""
        boleto_label = f" - Boleto {parcela['numero_boleto']}" if parcela["numero_boleto"] else ""
        grupo_items = list(grupos.items())
        valor_distribuido = Decimal("0")
        for grupo_index, ((categoria_id, subcategoria_id), grupo_total) in enumerate(grupo_items, start=1):
            categoria = db.get(Categoria, categoria_id)
            if grupo_index == len(grupo_items):
                valor_conta = Decimal(parcela["valor"]) - valor_distribuido
            else:
                valor_conta = (Decimal(parcela["valor"]) * grupo_total / base_produtos).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                valor_distribuido += valor_conta
            categoria_label = f" - {categoria.nome}" if len(grupo_items) > 1 and categoria else ""
            conta = ContaPagar(
                empresa_id=empresa_id,
                fornecedor_id=fornecedor_id,
                categoria_id=categoria_id,
                subcategoria_id=subcategoria_id,
                nota_fiscal_id=nota.id,
                numero_boleto=parcela["numero_boleto"],
                numero_parcela=index,
                total_parcelas=total_parcelas,
                data_vencimento=parcela["vencimento"] or vencimento,
                data_competencia=vencimento,
                descricao=f"NF-e {nota.numero_nf or nota.chave_acesso or nota.id}{parcela_label}{boleto_label}{categoria_label}",
                valor=valor_conta,
                origem=OrigemLancamento.xml,
            )
            db.add(conta)
            db.flush()
            contas.append(conta)
    return contas


def import_nfe_xml(db: Session, empresa_id: int, xml_content: str, item_categories: list[dict] | None = None) -> tuple[NotaFiscalXML, list[ContaPagar]]:
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise HTTPException(400, "XML inválido.") from exc

    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    inf = root.find(".//nfe:infNFe", ns) or root.find(".//infNFe")
    emit = root.find(".//nfe:emit", ns) or root.find(".//emit")
    total = root.find(".//nfe:ICMSTot", ns) or root.find(".//ICMSTot")
    if emit is None:
        raise HTTPException(400, "XML não parece conter emitente de NF-e.")

    chave = inf.attrib.get("Id", "").replace("NFe", "") if inf is not None else None
    numero = _text(root, ".//nfe:ide/nfe:nNF", ns) or _text(root, ".//ide/nNF", {})
    data_emissao_raw = _text(root, ".//nfe:ide/nfe:dhEmi", ns) or _text(root, ".//ide/dhEmi", {})
    data_emissao = datetime.fromisoformat(data_emissao_raw.replace("Z", "+00:00")) if data_emissao_raw else None
    cnpj = _text(emit, "nfe:CNPJ", ns) or _text(emit, "CNPJ", {})
    nome = _text(emit, "nfe:xNome", ns) or _text(emit, "xNome", {})
    valor_total = _decimal(_text(total, "nfe:vNF", ns) or _text(total, "vNF", {}) if total is not None else None)
    impostos = _decimal(_text(total, "nfe:vTotTrib", ns) or _text(total, "vTotTrib", {}) if total is not None else None)

    if chave and db.scalar(select(NotaFiscalXML).where(NotaFiscalXML.chave_acesso == chave)):
        raise HTTPException(409, "Nota fiscal já importada.")

    fornecedor = db.scalar(select(Fornecedor).where(Fornecedor.empresa_id == empresa_id, Fornecedor.cpf_cnpj == cnpj))
    if not fornecedor:
        fornecedor = Fornecedor(empresa_id=empresa_id, nome_razao_social=nome or "Fornecedor NF-e", cpf_cnpj=cnpj)
        db.add(fornecedor)
        db.flush()
    cliente = ensure_cliente_from_nfe(db, empresa_id, cnpj, nome, numero)

    nota = NotaFiscalXML(
        empresa_id=empresa_id,
        fornecedor_id=fornecedor.id,
        numero_nf=numero,
        chave_acesso=chave,
        data_emissao=data_emissao,
        cnpj_emitente=cnpj,
        nome_emitente=nome,
        valor_total=valor_total,
        impostos=impostos,
        arquivo_xml=xml_content,
    )
    db.add(nota)
    db.flush()

    produtos_importados = []
    for index, det in enumerate(_descendants_by_local_name(root, "det")):
        prod = (_children_by_local_name(det, "prod") or [None])[0]
        if prod is None:
            continue
        assignment = (item_categories or [])[index] if index < len(item_categories or []) else {}
        categoria, sub = get_selected_category(
            db,
            empresa_id,
            int(assignment.get("categoria_id")) if assignment.get("categoria_id") else None,
            int(assignment.get("subcategoria_id")) if assignment.get("subcategoria_id") else None,
        )
        produto = ProdutoNota(
            nota_fiscal_id=nota.id,
            categoria_id=categoria.id,
            subcategoria_id=sub.id,
            codigo=_text(prod, "nfe:cProd", ns) or _text(prod, "cProd", {}),
            descricao=_text(prod, "nfe:xProd", ns) or _text(prod, "xProd", {}) or "Produto",
            quantidade=_decimal(_text(prod, "nfe:qCom", ns) or _text(prod, "qCom", {})),
            valor_unitario=_decimal(_text(prod, "nfe:vUnCom", ns) or _text(prod, "vUnCom", {})),
            valor_total=_decimal(_text(prod, "nfe:vProd", ns) or _text(prod, "vProd", {})),
        )
        db.add(produto)
        produtos_importados.append(produto)

    db.flush()
    contas = rebuild_contas_pagar_from_nota(db, nota, empresa_id)
    for conta in contas:
        categoria = db.get(Categoria, conta.categoria_id)
        sub = db.get(Subcategoria, conta.subcategoria_id)
        db.add(HistoricoAlteracao(
            empresa_id=empresa_id,
            entidade="contas_pagar",
            entidade_id=conta.id,
            acao="importado_xml",
            dados=f"Conta criada pela NF-e {numero or chave or nota.id}, boleto {conta.numero_boleto or '-'}, categoria {categoria.nome if categoria else '-'} / {sub.nome if sub else '-'}, fornecedor {nome or fornecedor.nome_razao_social}, cliente {cliente.nome_razao_social}.",
        ))
    db.commit()
    db.refresh(nota)
    for conta in contas:
        db.refresh(conta)
    return nota, contas

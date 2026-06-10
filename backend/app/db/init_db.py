from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.entities import Categoria, Cliente, ContaPagar, ContaReceber, Empresa, Fornecedor, Subcategoria, Usuario
from app.models.enums import TipoCategoria


DEFAULT_CATEGORIES = {
    TipoCategoria.receita: {
        "Vendas": ["Vendas"],
    },
    TipoCategoria.despesa: {
        "Despesas com Venda": ["Comissão", "Frete", "Cupom", "Rebate"],
        "Compras": ["Produtos", "Embalagens"],
        "Despesas com Publicidade": [
            "Mercado Livre ADS",
            "Shopee ADS",
            "Magalu ADS",
            "Amazon ADS",
            "Tiktok ADS",
            "Mercado Livre Afiliados",
            "Shopee Afiliados",
            "Tiktok Afiliados",
        ],
        "Despesas com Logistica": [
            "Armazenamento Full",
            "Armazenamento Prolongado Full",
            "Inconformidade Full",
            "Retirada Full",
            "Coleta Full",
            "Frete",
        ],
        "Despesas com Pessoal": [
            "Salários",
            "Férias",
            "13º Salário",
            "FGTS",
            "INSS",
            "Rescisão",
            "Vale Alimentação",
            "Vale Transporte",
        ],
        "Aluguel": ["Aluguel"],
        "Despesas Financeiras": ["Pacote de Serviços"],
        "Empréstimos": ["Empréstimos"],
        "Gasto com Sistemas": ["ERP", "Calculadora MKP", "FluxoPro"],
        "Gastos com Veículos": ["Gasolina", "Pedágio", "IPVA", "Manutenção de Veiculo"],
        "Impostos": ["ICMS", "IPI", "PIS", "COFINS", "CSLL", "IRPJ", "DAS", "DAS-MEI", "CBS", "IBS"],
        "Investimento": ["Investimento"],
        "Manutenção": ["Material para Manutenção", "Mão de obra para Manutenção"],
        "Manutenção Operacional": ["Água", "Energia", "Internet", "Supermercado", "Material de escritório"],
        "Parcelamento": ["Parcelamento de DAS", "Parcelamento de ICMS", "Parcelamento IPI"],
        "Pró-Labore": ["Pró-Labore"],
        "Retirade de Lucro": ["Retirade de Lucro"],
        "Serviços": ["Contabilidade", "Advocacia"],
        "Transferências": ["Transferências"],
    },
}

LEGACY_DEFAULT_CATEGORIES = {
    (TipoCategoria.receita, "Marketplace"),
    (TipoCategoria.receita, "Serviços"),
    (TipoCategoria.receita, "Outras Receitas"),
    (TipoCategoria.despesa, "Fornecedores"),
    (TipoCategoria.despesa, "Marketing"),
    (TipoCategoria.despesa, "Fretes"),
    (TipoCategoria.despesa, "Folha de Pagamento"),
    (TipoCategoria.despesa, "Administrativo"),
    (TipoCategoria.despesa, "Tecnologia"),
    (TipoCategoria.despesa, "Operacional"),
    (TipoCategoria.despesa, "Outras Despesas"),
}


def seed_database(db: Session) -> None:
    empresa = db.scalar(select(Empresa).where(Empresa.nome == "Empresa Demonstração"))
    if not empresa:
        empresa = Empresa(nome="Empresa Demonstração", cnpj="00000000000100")
        db.add(empresa)
        db.flush()

    user = db.scalar(select(Usuario).where(Usuario.email == "admin@demo.com"))
    if not user:
        db.add(Usuario(
            empresa_id=empresa.id,
            nome="Administrador",
            email="admin@demo.com",
            hashed_password=get_password_hash("admin123"),
        ))

    categories: dict[str, Categoria] = {}
    for tipo, category_map in DEFAULT_CATEGORIES.items():
        for name, subcategory_names in category_map.items():
            category = db.scalar(select(Categoria).where(Categoria.empresa_id == empresa.id, Categoria.nome == name, Categoria.tipo == tipo))
            if not category:
                category = Categoria(empresa_id=empresa.id, nome=name, tipo=tipo)
                db.add(category)
                db.flush()
            category.ativa = True
            categories[name] = category
            for subcategory_name in subcategory_names:
                subcategory = db.scalar(select(Subcategoria).where(Subcategoria.categoria_id == category.id, Subcategoria.nome == subcategory_name))
                if not subcategory:
                    subcategory = Subcategoria(categoria_id=category.id, nome=subcategory_name)
                    db.add(subcategory)
                subcategory.ativa = True
            geral = db.scalar(select(Subcategoria).where(Subcategoria.categoria_id == category.id, Subcategoria.nome == "Geral"))
            if geral and "Geral" not in subcategory_names:
                geral.ativa = False

    for tipo, name in LEGACY_DEFAULT_CATEGORIES:
        legacy_category = db.scalar(select(Categoria).where(Categoria.empresa_id == empresa.id, Categoria.nome == name, Categoria.tipo == tipo))
        if legacy_category:
            legacy_category.ativa = False

    if not db.scalar(select(Cliente).where(Cliente.empresa_id == empresa.id)):
        cliente = Cliente(empresa_id=empresa.id, nome_razao_social="Cliente Exemplo", cpf_cnpj="12345678000190", email="cliente@example.com")
        fornecedor = Fornecedor(empresa_id=empresa.id, nome_razao_social="Fornecedor Exemplo", cpf_cnpj="98765432000110", email="fornecedor@example.com")
        db.add_all([cliente, fornecedor])
        db.flush()
        vendas_sub = db.scalar(select(Subcategoria).where(Subcategoria.categoria_id == categories["Vendas"].id))
        compra_sub = db.scalar(select(Subcategoria).where(Subcategoria.categoria_id == categories["Compras"].id, Subcategoria.nome == "Produtos"))
        db.add_all([
            ContaReceber(
                empresa_id=empresa.id,
                cliente_id=cliente.id,
                categoria_id=categories["Vendas"].id,
                subcategoria_id=vendas_sub.id,
                data_vencimento=date.today() + timedelta(days=7),
                data_competencia=date.today(),
                descricao="Venda demonstrativa em aberto",
                valor=Decimal("8500.00"),
            ),
            ContaPagar(
                empresa_id=empresa.id,
                fornecedor_id=fornecedor.id,
                categoria_id=categories["Compras"].id,
                subcategoria_id=compra_sub.id,
                data_vencimento=date.today() + timedelta(days=5),
                data_competencia=date.today(),
                descricao="Compra demonstrativa em aberto",
                valor=Decimal("3200.00"),
            ),
        ])
    db.commit()


def ensure_sqlite_schema(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(contas_pagar)")).all()}
    additions = {
        "numero_boleto": "ALTER TABLE contas_pagar ADD COLUMN numero_boleto VARCHAR",
        "numero_parcela": "ALTER TABLE contas_pagar ADD COLUMN numero_parcela INTEGER",
        "total_parcelas": "ALTER TABLE contas_pagar ADD COLUMN total_parcelas INTEGER",
    }
    for column, statement in additions.items():
        if column not in columns:
            db.execute(text(statement))
    product_columns = {row[1] for row in db.execute(text("PRAGMA table_info(produtos_notas)")).all()}
    product_additions = {
        "categoria_id": "ALTER TABLE produtos_notas ADD COLUMN categoria_id INTEGER",
        "subcategoria_id": "ALTER TABLE produtos_notas ADD COLUMN subcategoria_id INTEGER",
    }
    for column, statement in product_additions.items():
        if column not in product_columns:
            db.execute(text(statement))
    db.commit()

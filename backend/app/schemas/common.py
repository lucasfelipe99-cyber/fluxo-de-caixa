from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.enums import (
    OrigemLancamento,
    StatusCadastro,
    StatusPagar,
    StatusReceber,
    TipoCategoria,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class EmpresaOut(ORMModel):
    id: int
    nome: str
    cnpj: str | None = None
    ativa: bool


class PessoaBase(BaseModel):
    nome_razao_social: str
    cpf_cnpj: str | None = None
    telefone: str | None = None
    email: EmailStr | None = None
    endereco: str | None = None
    observacoes: str | None = None
    data_cadastro: date | None = None
    status: StatusCadastro = StatusCadastro.ativo


class PessoaCreate(PessoaBase):
    pass


class PessoaUpdate(BaseModel):
    nome_razao_social: str | None = None
    cpf_cnpj: str | None = None
    telefone: str | None = None
    email: EmailStr | None = None
    endereco: str | None = None
    observacoes: str | None = None
    status: StatusCadastro | None = None


class PessoaOut(ORMModel, PessoaBase):
    id: int
    empresa_id: int
    created_at: datetime


class CategoriaCreate(BaseModel):
    nome: str
    tipo: TipoCategoria
    ativa: bool = True


class CategoriaUpdate(BaseModel):
    nome: str | None = None
    tipo: TipoCategoria | None = None
    ativa: bool | None = None


class SubcategoriaCreate(BaseModel):
    categoria_id: int
    nome: str
    ativa: bool = True


class SubcategoriaUpdate(BaseModel):
    nome: str | None = None
    ativa: bool | None = None


class SubcategoriaOut(ORMModel):
    id: int
    categoria_id: int
    nome: str
    ativa: bool


class CategoriaOut(ORMModel):
    id: int
    empresa_id: int
    nome: str
    tipo: TipoCategoria
    ativa: bool
    subcategorias: list[SubcategoriaOut] = []


class ContaPagarCreate(BaseModel):
    data_vencimento: date
    data_competencia: date
    fornecedor_id: int | None = None
    nota_fiscal_id: int | None = None
    numero_boleto: str | None = None
    numero_parcela: int | None = None
    total_parcelas: int | None = None
    descricao: str
    categoria_id: int
    subcategoria_id: int
    valor: Decimal
    status: StatusPagar = StatusPagar.aberto


class ContaPagarUpdate(BaseModel):
    data_vencimento: date | None = None
    data_competencia: date | None = None
    fornecedor_id: int | None = None
    nota_fiscal_id: int | None = None
    numero_boleto: str | None = None
    numero_parcela: int | None = None
    total_parcelas: int | None = None
    descricao: str | None = None
    categoria_id: int | None = None
    subcategoria_id: int | None = None
    valor: Decimal | None = None
    status: StatusPagar | None = None


class ContaPagarOut(ORMModel):
    id: int
    empresa_id: int
    data_vencimento: date
    data_competencia: date
    fornecedor_id: int | None
    descricao: str
    categoria_id: int
    subcategoria_id: int
    valor: Decimal
    valor_pago: Decimal
    status: StatusPagar
    origem: OrigemLancamento
    nota_fiscal_id: int | None
    numero_boleto: str | None
    numero_parcela: int | None
    total_parcelas: int | None


class BaixaFinanceiraOut(ORMModel):
    id: int
    data_baixa: date
    valor: Decimal
    observacao: str | None
    created_at: datetime


class HistoricoAlteracaoOut(ORMModel):
    id: int
    entidade: str
    entidade_id: int
    acao: str
    dados: str | None
    created_at: datetime


class ContaPagarListItem(ContaPagarOut):
    fornecedor_nome: str | None = None
    categoria_nome: str | None = None
    subcategoria_nome: str | None = None
    numero_nf: str | None = None
    cnpj_emitente: str | None = None
    nome_emitente: str | None = None
    cliente_nf_id: int | None = None
    cliente_nf_nome: str | None = None
    saldo_restante: Decimal


class ContaPagarDetail(ContaPagarListItem):
    baixas: list[BaixaFinanceiraOut] = []
    historico: list[HistoricoAlteracaoOut] = []


class ContaReceberCreate(BaseModel):
    data_vencimento: date
    data_competencia: date
    cliente_id: int | None = None
    descricao: str
    categoria_id: int
    subcategoria_id: int
    valor: Decimal
    status: StatusReceber = StatusReceber.aberto


class ContaReceberUpdate(BaseModel):
    data_vencimento: date | None = None
    data_competencia: date | None = None
    cliente_id: int | None = None
    descricao: str | None = None
    categoria_id: int | None = None
    subcategoria_id: int | None = None
    valor: Decimal | None = None
    status: StatusReceber | None = None


class ContaReceberOut(ORMModel):
    id: int
    empresa_id: int
    data_vencimento: date
    data_competencia: date
    cliente_id: int | None
    descricao: str
    categoria_id: int
    subcategoria_id: int
    valor: Decimal
    valor_recebido: Decimal
    status: StatusReceber


class BaixaPayload(BaseModel):
    data_baixa: date
    valor: Decimal
    observacao: str | None = None


class ProdutoNotaOut(ORMModel):
    id: int
    codigo: str | None
    descricao: str
    quantidade: Decimal
    valor_unitario: Decimal
    valor_total: Decimal
    categoria_id: int | None = None
    subcategoria_id: int | None = None


class NotaFiscalOut(ORMModel):
    id: int
    fornecedor_id: int | None
    numero_nf: str | None
    chave_acesso: str | None
    data_emissao: datetime | None
    cnpj_emitente: str | None
    nome_emitente: str | None
    valor_total: Decimal
    impostos: Decimal
    produtos: list[ProdutoNotaOut] = []


class DashboardOut(BaseModel):
    kpis: dict[str, Decimal]
    evolucao_caixa: list[dict]
    receitas_por_categoria: list[dict]
    despesas_por_categoria: list[dict]
    resultado_mensal: list[dict]

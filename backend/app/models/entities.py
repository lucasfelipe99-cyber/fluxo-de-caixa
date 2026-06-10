from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin
from app.models.enums import (
    OrigemLancamento,
    StatusCadastro,
    StatusPagar,
    StatusReceber,
    TipoBaixa,
    TipoCategoria,
)


class Empresa(TimestampMixin, Base):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(index=True)
    cnpj: Mapped[str | None] = mapped_column(index=True)
    ativa: Mapped[bool] = mapped_column(default=True)


class Usuario(TimestampMixin, Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    nome: Mapped[str]
    email: Mapped[str] = mapped_column(unique=True, index=True)
    hashed_password: Mapped[str]
    ativo: Mapped[bool] = mapped_column(default=True)


class PessoaMixin(TimestampMixin):
    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    nome_razao_social: Mapped[str] = mapped_column(index=True)
    cpf_cnpj: Mapped[str | None] = mapped_column(index=True)
    telefone: Mapped[str | None]
    email: Mapped[str | None]
    endereco: Mapped[str | None] = mapped_column(Text)
    observacoes: Mapped[str | None] = mapped_column(Text)
    data_cadastro: Mapped[date] = mapped_column(Date, default=date.today)
    status: Mapped[StatusCadastro] = mapped_column(Enum(StatusCadastro), default=StatusCadastro.ativo)


class Cliente(PessoaMixin, Base):
    __tablename__ = "clientes"


class Fornecedor(PessoaMixin, Base):
    __tablename__ = "fornecedores"


class Categoria(TimestampMixin, Base):
    __tablename__ = "categorias"
    __table_args__ = (UniqueConstraint("empresa_id", "nome", "tipo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    nome: Mapped[str] = mapped_column(index=True)
    tipo: Mapped[TipoCategoria] = mapped_column(Enum(TipoCategoria), index=True)
    ativa: Mapped[bool] = mapped_column(default=True)
    subcategorias: Mapped[list["Subcategoria"]] = relationship(cascade="all, delete-orphan")


class Subcategoria(TimestampMixin, Base):
    __tablename__ = "subcategorias"
    __table_args__ = (UniqueConstraint("categoria_id", "nome"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), index=True)
    nome: Mapped[str]
    ativa: Mapped[bool] = mapped_column(default=True)


class NotaFiscalXML(TimestampMixin, Base):
    __tablename__ = "notas_fiscais_xml"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id"), index=True)
    numero_nf: Mapped[str | None] = mapped_column(index=True)
    chave_acesso: Mapped[str | None] = mapped_column(unique=True, index=True)
    data_emissao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cnpj_emitente: Mapped[str | None] = mapped_column(index=True)
    nome_emitente: Mapped[str | None]
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    impostos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    arquivo_xml: Mapped[str] = mapped_column(Text)
    produtos: Mapped[list["ProdutoNota"]] = relationship(cascade="all, delete-orphan")


class ProdutoNota(TimestampMixin, Base):
    __tablename__ = "produtos_notas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nota_fiscal_id: Mapped[int] = mapped_column(ForeignKey("notas_fiscais_xml.id"), index=True)
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"), index=True)
    subcategoria_id: Mapped[int | None] = mapped_column(ForeignKey("subcategorias.id"), index=True)
    codigo: Mapped[str | None]
    descricao: Mapped[str]
    quantidade: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    valor_unitario: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    categoria: Mapped[Categoria | None] = relationship(foreign_keys=[categoria_id])
    subcategoria: Mapped[Subcategoria | None] = relationship(foreign_keys=[subcategoria_id])


class ContaPagar(TimestampMixin, Base):
    __tablename__ = "contas_pagar"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id"), index=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), index=True)
    subcategoria_id: Mapped[int] = mapped_column(ForeignKey("subcategorias.id"), index=True)
    nota_fiscal_id: Mapped[int | None] = mapped_column(ForeignKey("notas_fiscais_xml.id"), index=True)
    numero_boleto: Mapped[str | None] = mapped_column(index=True)
    numero_parcela: Mapped[int | None]
    total_parcelas: Mapped[int | None]
    data_vencimento: Mapped[date] = mapped_column(Date, index=True)
    data_competencia: Mapped[date] = mapped_column(Date, index=True)
    descricao: Mapped[str]
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valor_pago: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[StatusPagar] = mapped_column(Enum(StatusPagar), default=StatusPagar.aberto, index=True)
    origem: Mapped[OrigemLancamento] = mapped_column(Enum(OrigemLancamento), default=OrigemLancamento.manual)
    fornecedor: Mapped[Fornecedor | None] = relationship()
    categoria: Mapped[Categoria] = relationship()
    subcategoria: Mapped[Subcategoria] = relationship()
    nota_fiscal: Mapped[NotaFiscalXML | None] = relationship()


class ContaReceber(TimestampMixin, Base):
    __tablename__ = "contas_receber"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"), index=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), index=True)
    subcategoria_id: Mapped[int] = mapped_column(ForeignKey("subcategorias.id"), index=True)
    data_vencimento: Mapped[date] = mapped_column(Date, index=True)
    data_competencia: Mapped[date] = mapped_column(Date, index=True)
    descricao: Mapped[str]
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valor_recebido: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[StatusReceber] = mapped_column(Enum(StatusReceber), default=StatusReceber.aberto, index=True)
    cliente: Mapped[Cliente | None] = relationship()
    categoria: Mapped[Categoria] = relationship()
    subcategoria: Mapped[Subcategoria] = relationship()


class BaixaFinanceira(TimestampMixin, Base):
    __tablename__ = "baixas_financeiras"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    tipo: Mapped[TipoBaixa] = mapped_column(Enum(TipoBaixa), index=True)
    conta_pagar_id: Mapped[int | None] = mapped_column(ForeignKey("contas_pagar.id"), index=True)
    conta_receber_id: Mapped[int | None] = mapped_column(ForeignKey("contas_receber.id"), index=True)
    data_baixa: Mapped[date] = mapped_column(Date, index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    observacao: Mapped[str | None] = mapped_column(Text)


class HistoricoAlteracao(TimestampMixin, Base):
    __tablename__ = "historico_alteracoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    entidade: Mapped[str] = mapped_column(index=True)
    entidade_id: Mapped[int] = mapped_column(index=True)
    acao: Mapped[str]
    dados: Mapped[str | None] = mapped_column(Text)


Index("ix_pagar_empresa_status_vencimento", ContaPagar.empresa_id, ContaPagar.status, ContaPagar.data_vencimento)
Index("ix_receber_empresa_status_vencimento", ContaReceber.empresa_id, ContaReceber.status, ContaReceber.data_vencimento)

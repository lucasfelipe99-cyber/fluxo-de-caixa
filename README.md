# Fluxo Caixa Empresarial

Sistema financeiro empresarial em Python + React para gestão de fluxo de caixa, contas a pagar, contas a receber, XML de NF-e, relatórios e dashboard executivo.

## Stack

- Backend: FastAPI, SQLAlchemy, Pydantic, JWT
- Frontend: React, Vite, Recharts, CSS responsivo
- Banco: SQLite em desenvolvimento, PostgreSQL via `DATABASE_URL`
- Exportações: Excel e PDF no relatório mensal

## Funcionalidades incluídas

- Login JWT com usuário demo
- Estrutura multiempresa no banco
- CRUD de clientes, fornecedores, categorias e subcategorias
- Contas a pagar e receber com baixa financeira parcial ou total
- Importação básica de XML NF-e, criação automática do fornecedor e lançamento em contas a pagar
- Fluxo de caixa realizado e projetado
- Dashboard com KPIs e gráficos
- Relatório mensal/anual e exportação mensal em Excel/PDF
- Modelos para histórico de alterações, produtos de notas e baixas financeiras

## Como rodar

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

A API ficará em `http://localhost:8000`.

Credenciais demo:

- E-mail: `admin@demo.com`
- Senha: `admin123`

### Frontend

Em outro terminal:

```bash
cd frontend
npm.cmd install
copy .env.example .env
npm.cmd run dev
```

A interface ficará em `http://localhost:5173`.

Se `npm install` ou `pip install` falhar com erros como `EBADF`, `EPERM` ou `unknown error, write` dentro de uma pasta sincronizada pelo Google Drive, copie o projeto para uma pasta local não sincronizada, como `C:\Projetos\FLUXO-DE-CAIXA`, e execute a instalação novamente.

## Estrutura

```text
backend/
  app/
    core/          Configuração e segurança
    db/            Sessão SQLAlchemy e seed inicial
    models/        Tabelas relacionais
    repositories/  Helpers de persistência
    routes/        Endpoints FastAPI
    schemas/       Schemas Pydantic
    services/      Regras financeiras e leitor XML
frontend/
  src/
    api/           Cliente HTTP
    lib/           Formatação
    main.jsx       Aplicação e telas
    styles.css     Design system responsivo
```

## Produção

Para PostgreSQL, ajuste o `DATABASE_URL` no backend, por exemplo:

```env
DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/financeiro
```

Para uma implantação real, os próximos passos recomendados são adicionar Alembic para migrations versionadas, rotina de backup agendada, testes automatizados, permissões por perfil e telas dedicadas de edição avançada.

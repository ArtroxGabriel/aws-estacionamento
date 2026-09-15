# AGENTS.md - Frontend Web (React / Vite)

## Project Overview
- Interface Web para simulação de totem de entrada (captura/upload de foto do veículo) e painel administrativo do operador (mapa de vagas disponíveis em tempo real, consulta de histórico e liberação de saída com cálculo de pagamento).

## Tech Stack
- **Runtime / Framework**: React + Vite (Node 26)
- **Linguagem**: TypeScript
- **Estilização**: Tailwind CSS

## File Structure
```text
web/
├── src/
│   ├── components/         # Totem de entrada, display de vagas, modal de pagamento
│   ├── pages/              # Painel de vagas, Entrada de veículos, Histórico/Auditoria
│   ├── services/           # Cliente HTTP da API Go
│   └── types/              # Tipagens das sessões, tickets e vagas
├── package.json
└── vite.config.ts
```

## Common Commands
- `task dev:web` ou `npm run dev`: Servidor de desenvolvimento
- `task build:web` ou `npm run build`: Build de produção (arquivos estáticos servidos no EC2)
- `npm test`: Testes de componentes

## Architecture Conventions
- Integração exclusiva com a API Go (`POST /entradas`, `GET /vagas/disponiveis`, `POST /saidas/:id/pagar`).
- Polling ou refresh otimizado para manter o contador de vagas sincronizado com o Redis.

## Changelog
- 2026-09-15: Criação do AGENTS.md do Frontend Web.

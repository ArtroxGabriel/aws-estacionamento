# Plano: a foto deixa de bloquear o ticket

> Branch: `feat/photo-optional-ticket-flow`. Relacionado: [PLANO-TP2.md](PLANO-TP2.md) e o plano do worker (`docs/PLANO-TP2-WORKER.md`, na branch `feat/worker-rabbitmq-migration`).

## 1. Problema

Hoje a leitura da placa é uma **etapa obrigatória** do ciclo da sessão:

```
PROCESSING ──(OCR leu)──► PARKED ──► PAID
     └──(OCR não leu)──► FAILED ──(caixa digita a placa)──► PARKED
```

Consequências:

- Quando o OCR não lê a placa, a sessão fica `FAILED` e **alguém precisa validar o ticket manualmente** (digitar a placa no caixa) para ela voltar ao fluxo normal.
- A vaga só é descontada quando a sessão vira `PARKED`. Enquanto está `PROCESSING` ou `FAILED`, o carro está dentro do estacionamento, mas o painel mostra a vaga como livre.
- Só com o Tesseract (Trabalho 2, sem Rekognition) cerca de metade das fotos reais não é lida, então o caso "manual" vira o caso comum.
- O worker carrega a parte mais delicada do sistema (transação RDS + Redis + DynamoDB com compensação) só porque é ele quem desconta a vaga.

## 2. Princípio

> **O ticket identifica o carro. A foto é evidência para auditoria e um atalho para a saída.**

- A sessão é válida e ocupa a vaga **no momento da entrada**, com ou sem placa.
- O OCR só **enriquece** a sessão com a placa, quando consegue.
- Pagar e sair funcionam sempre pelo **ID do ticket**. A placa, quando existe, permite achar a sessão mais rápido e abrir a cancela de saída sem o ticket.
- Nenhuma falha de OCR, de fila ou de storage exige ação humana para o carro seguir o fluxo.

## 3. Novo modelo

### 3.1 Estados

`status` passa a descrever só **onde o carro está**; a leitura da placa ganha um campo próprio.

| Campo | Valores | Quem altera |
|---|---|---|
| `status` | `PARKED` → `PAID` → `EXITED` | API |
| `plate_status` (novo) | `PENDING` → `READ` \| `UNREADABLE`; `MANUAL` quando o operador digita | Worker (`READ`, `UNREADABLE`) e API (`PENDING`, `MANUAL`) |

`PROCESSING` e `FAILED` deixam de existir como `status`.

> Se a parte de cancela de saída do Trabalho 2 (`EXITED`) ainda não estiver na `main` quando esta branch for implementada, o ciclo fica `PARKED` → `PAID` e a vaga é devolvida no pagamento, como hoje. O restante do plano não muda.

### 3.2 Regras de negócio

| Evento | Hoje | Novo |
|---|---|---|
| **Entrada** (`POST /entries`) | Cria `PROCESSING`; vaga não é descontada | Cria `PARKED` com `plate_status=PENDING`; **desconta a vaga na hora**; estacionamento lotado → `409` e a cancela não abre |
| **Falha ao publicar na fila** | Sessão `FAILED`, entrada devolve erro | Entrada **dá certo**; `plate_status=UNREADABLE`; auditoria `ENTRY` registra o aviso |
| **OCR leu** | `PARKED` + desconta a vaga + auditoria | Grava a placa, `plate_status=READ`, auditoria `OCR_PROCESSING`. **Não toca na vaga** |
| **OCR não leu** | `FAILED`; caixa precisa digitar | `plate_status=UNREADABLE`, auditoria `OCR_FAILED`. Sessão segue `PARKED` |
| **Falha de infraestrutura na 3ª tentativa** | Mensagem na DLQ; sessão `PROCESSING` para sempre | Mensagem na DLQ **e** `plate_status=UNREADABLE`; nada fica pendente |
| **Pagamento** | Só `PARKED` ou `FAILED` | Qualquer sessão `PARKED`, pelo ID do ticket; busca por placa quando ela existe |
| **Digitar/corrigir placa** (`PATCH /sessions/{id}`) | Necessário para sair de `FAILED`; desconta a vaga | **Opcional**; `plate_status=MANUAL`; não mexe na vaga |
| **Saída por placa** | — (Trabalho 2) | Abre se existir **exatamente uma** sessão `PAID` com aquela placa; senão pede o ticket |
| **Saída por ticket** | — (Trabalho 2) | Sempre disponível |
| **Reconstrução do contador** | `total − COUNT(PARKED)` | `total − COUNT(status IN ('PARKED','PAID'))` |

### 3.3 Foto como auditoria

- Nova rota `GET /sessions/{id}/photo`: a API lê a foto do storage (o `Download` já existe na interface `BlobStorage`) e a devolve.
- O caixa, a cancela de saída e a tela de auditoria passam a mostrar a foto da entrada. O operador confere visualmente o carro quando há dúvida.
- Efeito colateral bom para o Trabalho 2: a aplicação passa a fazer **download** do storage pela interface, o que a rubrica do MinIO cobra ("upload/download de arquivos funcionando").

### 3.4 O que muda no worker

O worker deixa de ser dono da vaga, então fica bem mais simples:

- Sai o Redis do worker: `storage/spots.py`, `REDIS_URL` e a compensação com `INCR`.
- A porta de idempotência passa a ser `UPDATE sessions SET license_plate=?, plate_status='READ' WHERE id=? AND plate_status='PENDING'`.
- A transação fica só RDS + auditoria no DynamoDB; não há mais efeito para compensar.
- Placa lida depois de o carro já ter saído (`EXITED`) ou de o operador ter digitado (`MANUAL`): 0 linhas alteradas, mensagem confirmada, sem efeito.

## 4. Decisões a confirmar com a equipe

| # | Decisão | Recomendação |
|---|---|---|
| D-F1 | Estacionamento lotado na entrada | Recusar com `409` (a cancela não abre). Hoje o sistema nunca recusa; só o contador para em 0 |
| D-F2 | Placa lida igual à de outra sessão ativa (leitura errada ou duplicada) | Não bloquear a entrada; na saída por placa, se houver mais de uma `PAID`, exigir o ticket |
| D-F3 | Expor a foto pela API (`GET /sessions/{id}/photo`) ou por link temporário direto do storage | Pela API: funciona igual no compose, no Kubernetes e na AWS, sem expor o storage ao navegador |
| D-F4 | Manter `PATCH /sessions/{id}` | Manter como conveniência (a busca por placa e a saída por placa passam a funcionar para aquela sessão) |
| D-F5 | Registro em [DECISOES.md](DECISOES.md) | Nova decisão que substitui a D4 ("placa ilegível vira falha na hora; caixa digita a placa") |

## 5. Tarefas

### Fase 1 — Banco e API
- [x] 1.1 Migration `000002_add_plate_status`: coluna `plate_status VARCHAR(16) NOT NULL DEFAULT 'PENDING'`. Conversão dos dados existentes: `PARKED`/`PAID` com placa → `READ`; `FAILED` → `PARKED` + `UNREADABLE`; `PROCESSING` → `PARKED` + `PENDING`. A migration `down` desfaz.
- [x] 1.2 `model.Session`: campo `PlateStatus` (`plate_status` no JSON).
- [x] 1.3 `CreateEntry`: reservar a vaga antes de criar a sessão; lotado → `ErrParkingFull` → `409`. Se o upload ou o `INSERT` falharem, devolver a vaga.
  - O script `decrIfPositive` atual devolve `0` tanto ao ocupar a última vaga quanto quando já estava lotado. Criar `Reserve` (novo script Lua) que distingue os casos: vaga reservada, lotado ou chave ausente.
  - Chave ausente (Redis reiniciado): recalcular pelo Postgres, regravar e tentar a reserva de novo.
- [x] 1.4 `CreateEntry`: falha ao publicar na fila não derruba a entrada; grava `plate_status=UNREADABLE` e o aviso na auditoria.
- [x] 1.5 `PayExit`: aceitar só `PARKED`; remover o caso `FAILED`.
- [x] 1.6 `UpdatePlate`: `plate_status=MANUAL`; remover o `Decrement` do caso `FAILED`.
- [x] 1.7 `DeleteSession`: devolver a vaga para `PARKED` **e** `PAID`. *Implementado só para `PARKED`: sem o estado `EXITED` na `main`, a vaga ainda é devolvida no pagamento; incrementar também em `PAID` contaria duas vezes. Muda junto com as rotas da cancela de saída.*
- [x] 1.8 `CountActive`: `status IN ('PARKED','PAID')`. *Implementado só com `PARKED`, pelo mesmo motivo.*
- [x] 1.9 `GET /sessions/{id}/photo` (handler + service, com `Content-Type` do objeto; `404` se a sessão ou a foto não existirem).
- [x] 1.10 `GET /sessions`: filtro por `plate_status` (o caixa lista "sem placa").
- [x] 1.11 Testes de service e handler: entrada desconta a vaga; lotado devolve `409`; falha de fila mantém a entrada; pagamento sem placa; exclusão de sessão `PAID` devolve a vaga; rota da foto.

### Fase 2 — Worker
- [x] 2.1 `session_repo.py`: `plate_transition(session_id, plate)` com a nova condição (`plate_status='PENDING'`) e `mark_unreadable(session_id)`; remover `mark_failed`.
- [x] 2.2 Processamento: tirar o `DECR`, a compensação e o tratamento de `FAILED`; estados terminais passam a ser "`plate_status` diferente de `PENDING`".
- [x] 2.3 Na 3ª falha de infraestrutura, além da DLQ, marcar `UNREADABLE`.
- [x] 2.4 Remover `storage/spots.py`, `REDIS_URL` da configuração e do compose do worker, e os testes do contador.
- [x] 2.5 Atualizar `test_poller_side_effects.py` e `test_poison.py`: leitura depois de `EXITED`/`MANUAL` não altera nada; entrega repetida não grava a placa duas vezes; ilegível não muda `status`.

### Fase 3 — Frontend
- [x] 3.1 `types/api.ts`: `SessionStatus` sem `PROCESSING`/`FAILED`; novo `PlateStatus`.
- [x] 3.2 Entrada (`EntryPage`): o ticket aparece **válido de imediato** ("cancela aberta"); a placa surge quando for lida; some o aviso de "procure o operador". Lotado → mensagem clara.
- [x] 3.3 Caixa (`PaymentPage`): listar só `PARKED`; sessão sem placa aparece como "sem placa", sem alerta; mostrar a miniatura da foto; "Informar placa" vira ação opcional.
- [ ] 3.4 Cancela de saída (quando existir): tenta a placa; sem correspondência única → campo do ticket; mostra a foto da entrada para conferência.
- [x] 3.5 Auditoria (`AuditPage`): rótulo de `OCR_FAILED` deixa de ser "falha" (vira "placa não lida"); link para a foto.
- [x] 3.6 `StatusBadge` e testes das páginas.

### Fase 4 — Documentação
- [x] 4.1 [GOAL.md](GOAL.md): ciclo de vida, tabela de estados, regra do contador e contratos das rotas.
- [x] 4.2 [DECISOES.md](DECISOES.md): nova decisão substituindo a D4.
- [x] 4.3 `AGENTS.md` (raiz, `api/`, `worker/`, `web/`) e spec do worker (requisitos 6, 7 e 10).

### Fase 5 — Verificação
- [x] 5.1 Testes: `go test ./...`, `uv run pytest`, `npm test`.
- [x] 5.2 Entrada com foto legível: ticket válido na hora; vagas −1 **antes** do OCR; placa aparece em seguida.
- [x] 5.3 Entrada com foto ilegível (ou arquivo que não é imagem): ticket válido, vagas −1, `plate_status=UNREADABLE`; pagar e sair pelo ticket **sem nenhuma ação do operador**.
- [x] 5.4 Worker parado: entrada, pagamento e saída pelo ticket funcionam; ao religar o worker, as placas pendentes são preenchidas.
- [x] 5.5 Fila fora do ar: a entrada continua funcionando.
- [x] 5.6 Estacionamento lotado (`TOTAL_PARKING_SPOTS=1`): a 2ª entrada recebe `409`.
- [x] 5.7 Redis reiniciado: o painel recalcula `total − (PARKED + PAID)`. *Conferido com `total − PARKED`, a regra em vigor enquanto `EXITED` não existe (ver 1.7 e 1.8).*
- [x] 5.8 Foto visível no caixa e na auditoria.
- [x] 5.9 Banco com dados antigos (`PROCESSING`, `FAILED`): a migration converte e o contador bate depois de apagar a chave do Redis.

> Verificado em 2026-11-16 com as imagens desta branch (API, worker e web) numa rede Docker isolada: emulador Floci para S3/SQS/DynamoDB, `postgres:16-alpine` e `redis:7-alpine`, `TOTAL_PARKING_SPOTS=4` e a fila com `VisibilityTimeout` de 5 s para observar as reentregas. A fila "fora do ar" do item 5.5 foi simulada apagando a fila.

## 6. Impacto e riscos

| Item | Impacto | Mitigação |
|---|---|---|
| Contrato da API muda (`status` perde dois valores; novo `plate_status`) | Frontend e worker precisam ir juntos | Uma branch só, mesclada de uma vez; a migration converte os dados |
| Conflito com a migração do worker para RabbitMQ | As duas mexem no processamento do worker | Implementar esta **depois** da `feat/worker-rabbitmq-migration`: o `MessageProcessor` isolado lá fica menor aqui. Se vier antes, o plano do worker perde a parte do Redis |
| Conflito com as rotas de cancela do Trabalho 2 | `PayExit`, `CountActive` e o momento do `INCR` mudam nos dois | Combinar a ordem com quem fizer a API; a regra do contador desta proposta (`PARKED` + `PAID`) já é a correta para o Trabalho 2 |
| Placa lida errada associa a saída ao carro errado | A cancela abre para a sessão errada | Saída por placa só com correspondência única e sessão `PAID`; o pagamento é sempre pelo ticket; foto exibida para conferência |
| Entrada passa a depender do Redis para descontar a vaga | Redis fora do ar na entrada | Mesma regra de hoje: sem a chave, a API recalcula pelo Postgres e segue |
| A demonstração perde o "caixa corrige a placa" como passo obrigatório | Menos um fluxo para mostrar | Continua existindo como ação opcional; em troca dá para mostrar o sistema funcionando com o worker desligado |

## 7. Fora do escopo

- Melhorar a taxa de leitura do OCR.
- Ler a placa na cancela de saída a partir de uma foto (o plano geral ainda não decidiu quem faz esse OCR).
- Cobrança por tempo de permanência.

# Documentação de Uso de IA no Desenvolvimento

Este documento registra o uso de Inteligência Artificial no desenvolvimento deste projeto, garantindo transparência sobre as ferramentas, os prompts utilizados e as correções manuais aplicadas.

---

## 1. Ferramentas de IA Utilizadas

- **One Pro (Adapta):** Utilizado para arquitetura do projeto, definição de regras de negócio, lógica dos scripts e geração dos prompts refinados.
- **Cursor Agent (Modelo Claude-3.5-Sonnet):** Utilizado como agente de codificação para interpretar os prompts da Adapta e gerar os arquivos de código (`.py`), além de auxiliar na refatoração e testes.

---

## 2. Prompts de Engenharia (Gerados pela Adapta)

Documentação dos prompts exatos fornecidos pela Adapta para a geração de cada módulo.

### Módulo: Geração de Dados (`generate_data.py`)

**Prompt Utilizado:**

```
Crie um script `generate_data.py` que gere um dataset sintético de eventos de e-commerce (1M a 3M de linhas) exportado para `events.csv`.
**Requisitos Técnicos:**
- Use `numpy` e `pandas` para vetorização total (proibido loops for lentos).
- Schema: `event_id` (UUID), `user_id` (int), `ts` (timestamp UTC últimos 30 dias), `event_type` (purchase, refund, page_view, signup), `amount` (float), `country` (BR, US, FR, etc), `device`.
- Lógica de Negócio: Distribuir `event_type` com pesos realistas (ex: purchase 2-5%). `amount` deve seguir distribuição log-normal para compras.
- **Dirty Data:** Injete propositalmente 0.5% de dados sujos (timestamps futuros/inválidos, países nulos) e IDs duplicados para testar o pipeline.
- Seed fixa para reprodutibilidade.
```

### Módulo: Pipeline de Processamento (`pipeline.py`)

**Prompt Utilizado:**

```
Implemente o `pipeline.py` contendo a função `build_report(path_csv: str) -> dict`.
**Passos do Pipeline:**
1. Carregamento eficiente do CSV.
2. Limpeza: Filtrar linhas com `ts` inválido, normalizar `country` (uppercase, fillna='UNK'), remover `event_type` desconhecido.
3. Deduplicação por `event_id`.
4. **Cálculo de Métricas:**
   - DAU (Daily Active Users).
   - Funil de conversão diário (Pageview -> Signup -> Purchase).
   - Receita Líquida Diária (Soma Purchase - Soma Refund).
   - Top 10 países por receita.
   - Detecção de Anomalias (Z-Score > 3 na receita diária).
   - Retenção D1 (Usuários que voltaram no dia seguinte).
```

### Módulo: API (`api.py`)

**Prompt Utilizado:**

```
Crie uma API FastAPI em `api.py` para servir o relatório.
**Endpoints:**
- `GET /health`: Retorna status 200.
- `GET /report?file=events.csv`: Executa `build_report` e retorna o JSON.
**Requisitos:**
- Implementar Cache em memória (dicionário global) para não reprocessar o CSV se ele não mudou.
- Middleware para logar tempo de resposta das requisições.
- Tratamento de erros (404 se arquivo não existir, 500 para erros de processamento).
```

### Módulo: Testes (`tests/test_pipeline.py`)

**Prompt Utilizado:**

```
Crie testes unitários usando `pytest`. Use fixture `tmp_path` para criar CSVs falsos isolados.
**Cenários de Teste:**
1. Validar se linhas com `ts` inválido são removidas.
2. Validar cálculo de Receita Líquida (criar CSV com 1 compra de 100 e 1 refund de 30 -> esperar 70).
3. Validar Retenção D1 com um dataset controlado (usuário volta vs não volta).
4. Teste de integração simples no endpoint `/health`.
```

---

## 3. Correções e Refinamentos Manuais

Correções técnicas aplicadas após a geração inicial do código pelo Cursor:

### Problema 1: Depreciação de Timestamp no Pandas

- **O que aconteceu:** O código gerado usava `pd.Timestamp.utcnow().floor("S")`. O Pandas emitiu avisos sobre o alias "S" (maiúsculo) ser depreciado em favor de "s" (minúsculo) e mudanças na API do `utcnow`.
- **Correção:** Atualizado para `pd.Timestamp.now("UTC").floor("s")`.

### Problema 2: Tipagem em Dados Sujos

- **O que aconteceu:** Ao injetar dados sujos (strings inválidas) na coluna de data, o Pandas falhava se a coluna já estivesse tipada como datetime.
- **Correção:** Forçada a conversão da coluna para `object` antes da injeção de ruído e garantida a conversão correta de volta para datetime durante o pipeline de limpeza.

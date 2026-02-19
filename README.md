<div align="center">

# Analytics Pipeline de Eventos de E-commerce

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API%20Server-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Pandas](https://img.shields.io/badge/Pandas-Data%20Processing-150458?logo=pandas)](https://pandas.pydata.org/)
[![NumPy](https://img.shields.io/badge/NumPy-Numeric%20Computing-013243?logo=numpy)](https://numpy.org/)
[![Pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest)](https://docs.pytest.org/)  

</div>

---

### Visão Geral do Projeto

Este projeto implementa um **pipeline de ingestão e análise de eventos de e‑commerce** (um mini sistema de ETL) que processa interações de usuários do tipo:

- **page_view**
- **signup**
- **purchase**
- **refund**

O fluxo completo cobre:

- **Geração de dados sintéticos em alta escala** (`generate_data.py`);
- **Limpeza, normalização e cálculo de métricas analíticas** (`pipeline.py`);
- **Exposição das métricas via API HTTP** usando **FastAPI** (`api.py`), com **cache em memória** baseado em mtime do arquivo de entrada (`events.csv`).

O resultado principal é um arquivo `report.json` consolidando métricas de engajamento, receita, funil de conversão, países de maior receita, detecção de anomalias e retenção D1.

---

### Arquitetura e Fluxo de Dados

O sistema segue um fluxo de dados simples em 3 etapas:

1. **Geração** → `generate_data.py` cria `events.csv` com eventos sintéticos.
2. **Processamento / Métricas** → `pipeline.py` carrega `events.csv`, limpa os dados e gera o `report.json`.
3. **Serviço de API** → `api.py` expõe as métricas via endpoints REST, com cache em memória.

#### 1. Geração de Dados (`generate_data.py`)

O módulo `generate_data.py` é responsável por gerar um dataset sintético de eventos de e‑commerce utilizando **NumPy** e **Pandas**, de forma **100% vetorizada** (sem loops `for` em Python sobre as linhas).

- **Configuração principal**:
  - `N_ROWS = 1_000_000` (número padrão de linhas geradas).
  - `OUTPUT_CSV = "events.csv"` (arquivo de saída).
  - `USER_BASE_SIZE = 100_000` (tamanho aproximado da base de usuários).
  - `EVENT_TYPES = ["page_view", "signup", "purchase", "refund"]`.
  - `COUNTRIES = ["BR", "US", "MX", "CA", "UK", "DE", "FR", "JP", "AU", "IN"]` (diversos mercados).
  - `DEVICES = ["ios", "android", "web"]`.

- **Distribuição de tipos de evento** (`EVENT_TYPE_PROBS`):
  - `purchase`: **3%** (dentro da faixa 2–5%);
  - `refund`: **0.3%** (dentro da faixa 0.1–0.5%);
  - o restante (**96.7%**) é distribuído entre:
    - **90%** `page_view`;
    - **6.7%** `signup`.

- **Distribuição de valores (coluna `amount`)**:
  - Para eventos `purchase` e `refund`, os valores são gerados com **distribuição lognormal** (parâmetros `mu = log(50)`, `sigma = 0.5`), aproximando preços médios em torno de 50 unidades monetárias.
  - Compras (`purchase`) têm `amount` **positivo**.
  - Reembolsos (`refund`) têm `amount` **negativo** (tratados como saída de receita).

- **Timestamps**:
  - Os timestamps (`ts`) são distribuídos **uniformemente** ao longo dos **últimos 30 dias**, em resolução de segundos, usando operações vetorizadas com `pd.to_timedelta`.

- **Injeção proposital de dados sujos / anomalias** (`inject_dirty_data`):
  - Fração de linhas sujas: `DIRTY_FRACTION = 0.005` (**0.5%** das linhas).
  - Injeções realizadas de forma totalmente vetorizada:
    - **Timestamps inválidos**:
      - metade dos casos substituída por datas **no futuro** (agora + 30 dias);
      - metade por strings inválidas (`"not_a_timestamp"`).
    - **País nulo**:
      - alguns registros têm a coluna `country` setada para `NaN`.
    - **Tipos de evento inválidos**:
      - troca de `event_type` para o valor `"???"` em parte das linhas.
    - **Colisões de `event_id`**:
      - subset de linhas tem o `event_id` copiado de outras linhas, criando **IDs duplicados**.

Com isso, o dataset simula um cenário **realista de big data** com dados sujos, pronto para ser tratado pelo pipeline.

#### 2. Pipeline de Processamento (`pipeline.py`)

O módulo `pipeline.py` implementa um pipeline de **limpeza**, **normalização** e **cálculo de métricas** usando **Pandas/NumPy** de forma **vetorizada**, encapsulado principalmente na função `build_report`.

- **Carregamento e limpeza (`_load_and_clean`)**:
  - Leitura do CSV **sem parse inicial de datas**, para tolerar strings inválidas.
  - Conversão da coluna `ts` para datetime com:

    ```python
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    ```

  - Timestamps inválidos são convertidos para `NaT` e **removidos**.
  - Remoção de timestamps **no futuro** (relativos ao `now` em UTC).
  - Criação da coluna `date` (apenas data, sem hora) para agregações diárias.
  - Filtragem para manter apenas `event_type` válidos (`page_view`, `signup`, `purchase`, `refund`).
  - Remoção de linhas com `country` nulo.
  - Remoção de **`event_id` duplicados**, mantendo apenas a primeira ocorrência.

- **Cálculo das métricas (funções vetorizadas)**:
  - `_compute_range` → intervalo de datas da base válida.
  - `_compute_counts` → contagem de linhas **brutas** vs **válidas**, e quantas foram descartadas.
  - `_compute_dau` → **Daily Active Users** via `groupby("date")["user_id"].nunique()`.
  - `_compute_funnel` → funil de eventos diários (ver seção de métricas abaixo).
  - `_compute_revenue_daily` → receita líquida agregada por dia.
  - `_compute_top_countries` → países com maior receita líquida.
  - `_compute_anomalies` → anomalias diárias de receita via Z‑score.
  - `_compute_retention_d1` → retenção D1 por coortes de signup.

A função principal `build_report(input_csv)` orquestra tudo, devolvendo um dicionário com as métricas e salvando o resultado em `report.json`.

#### 3. API (`api.py` / FastAPI)

O módulo `api.py` expõe as métricas calculadas via uma API **FastAPI**.

- **Aplicação**:

  ```python
  app = FastAPI(title="Analytics API")
  ```

- **Cache em memória**:
  - Estrutura global: `results_cache: Dict[str, Tuple[float, Dict[str, Any]]]`.
  - Chave: nome do arquivo (ex.: `"events.csv"`).
  - Valor: tupla `(mtime, report)`:
    - `mtime`: timestamp de modificação do arquivo CSV;
    - `report`: dicionário com o relatório já calculado.
  - A cada requisição, o pipeline só é reexecutado se o `mtime` do arquivo tiver mudado (**cache por mtime**).

- **Middleware de tempo de resposta**:
  - Middleware HTTP que mede o tempo de processamento de cada requisição, loga no console e injeta o header `X-Process-Time` na resposta.

- **Endpoints disponíveis**:

  - `GET /health`  
    - Retorna um JSON simples: `{"status": "ok"}` para verificação de saúde.

  - `GET /report`  
    - Query param: `file` (opcional, padrão `"events.csv"`).
    - Se o arquivo existir, retorna o `report` correspondente (reusando o cache quando possível).
    - Em caso de erro:
      - 404 se o arquivo não existir;
      - 500 se o pipeline falhar por qualquer outro motivo.

---

### Métricas Calculadas (`report.json`)

O arquivo `report.json` consolidado pelo pipeline contém, entre outros, os seguintes blocos de métricas:

- **Daily Active Users (DAU)** (`report["dau"]`)
  - Lista de objetos por dia:
    - `date`: data no formato ISO (ex.: `"2026-02-10"`).
    - `dau`: número de usuários únicos com qualquer evento nesse dia.
  - Calculado de forma vetorizada com `groupby("date")["user_id"].nunique()`.

- **Funnel de Conversão (View → Signup → Purchase)** (`report["funnel"]`)
  - Métrica diária do funil de eventos:
    - `date`: data.
    - `pv`: quantidade de `page_view`.
    - `signup`: quantidade de `signup`.
    - `purchase`: quantidade de `purchase`.
    - `pv_to_signup`: taxa de conversão de page_view para signup.
    - `signup_to_purchase`: taxa de conversão de signup para purchase.
  - Implementado via `pivot_table` + divisões vetorizadas com `np.where`, evitando divisões por zero.

- **Net Revenue Diário (Purchase − Refund)** (`report["revenue_daily"]`)
  - Receita líquida diária:
    - A coluna `amount` já traz:
      - valores **positivos** para `purchase`;
      - valores **negativos** para `refund`.
    - A receita diária é a soma dos `amount` por `date`:

      ```python
      rev_series = df.groupby("date")["amount"].sum()
      ```

  - Resultado: lista com `{"date": "...", "net_revenue": ...}`.

- **Top Countries por Receita** (`report["top_countries"]`)
  - Agregação de receita líquida por país:
    - Agrupa `amount` por `country`;
    - Ordena por `net_revenue` decrescente;
    - Retorna apenas os **top N** países (padrão `top_n=10`).
  - Cada item contém:
    - `country`;
    - `net_revenue`.

- **Detecção de Anomalias (Z‑score > 3 sigma)** (`report["anomalies"]`)
  - A função `_compute_anomalies` recebe a lista `revenue_daily` e calcula:
    - média (`mean`) e desvio padrão (`std`) de `net_revenue`;
    - `z_score` por dia: \((\text{net_revenue} - mean) / std\).
  - Resultado: lista com:
    - `date`;
    - `net_revenue`;
    - `z_score`.
  - Clientes podem aplicar thresholds (por exemplo, \|z_score\| > 3) para identificar dias anômalos em termos de receita.

- **Retenção D1** (`report["retention_d1"]`)
  - Análise de retenção no dia seguinte ao signup, por coorte:
    - `cohort_date`: data do **primeiro signup** de cada usuário.
    - `users`: quantidade de usuários na coorte.
    - `retained`: quantos desses usuários tiveram **qualquer evento** na data `cohort_date + 1`.
    - `rate`: fração `retained / users`.
  - Implementado sem loops, com:
    - groupby para primeira data de signup;
    - cálculo vetorizado de `d1_date`;
    - `merge` com o conjunto de `(user_id, date)` únicos de todos os eventos.

---

### Guia de Instalação e Execução

#### 1. Requisitos

- **Python 3.11+** (ou versão compatível com o projeto).
- `pip` para instalação de dependências.

#### 2. Clonar o repositório

```bash
git clone https://github.com/opauloobruuno/Projeto_Stonia.git
cd Projeto_Stonia
```

#### 3. Instalar dependências

Devido a existência de `requirements.txt`, utilize:

```bash
pip install -r requirements.txt
```

#### 4. Gerar os dados sintéticos

Por padrão, o script gera **1.000.000 de linhas**. Você pode ajustar o volume editando a constante `N_ROWS` em `generate_data.py`.

```bash
python generate_data.py
```

Isso criará o arquivo:

- `events.csv` – dataset bruto com dados limpos + sujos injetados.

#### 5. Rodar o pipeline e gerar o relatório

Você pode executar o pipeline diretamente pela linha de comando:

```bash
python pipeline.py
```

Ou simplesmente chamar `build_report` via módulo:

```bash
python -c "from pipeline import build_report; build_report('events.csv')"
```

Isso gerará:

- `report.json` – arquivo consolidando todas as métricas descritas acima.

#### 6. Iniciar o servidor FastAPI

Execute o servidor local usando `uvicorn` apontando para a aplicação definida em `api.py`:

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

Ou, se preferir, use o bloco `if __name__ == "__main__"` já presente em `api.py`:

```bash
python api.py
```

#### 7. Testar os endpoints

- **Health check**:

  ```bash
  curl http://localhost:8000/health
  ```

  Resposta esperada:

  ```json
  {"status": "ok"}
  ```

- **Obter o relatório analítico**:

  Certifique-se de que `events.csv` exista (gere com `generate_data.py` se necessário) e chame:

  ```bash
  curl "http://localhost:8000/report?file=events.csv"
  ```

  A resposta será um JSON grande com as chaves:

  ```json
  {
    "range": {...},
    "counts": {...},
    "dau": [...],
    "funnel": [...],
    "revenue_daily": [...],
    "top_countries": [...],
    "anomalies": [...],
    "retention_d1": [...]
  }
  ```

#### 8. Rodar os testes

O projeto traz testes automatizados (por exemplo, em `test_pipeline.py`) utilizando **pytest**.

```bash
pytest
```

---

### Decisões Técnicas

- **Uso pesado de operações vetorizadas (Pandas/NumPy)**:
  - Todas as etapas críticas (geração, limpeza e métricas) são implementadas com **operações vetorizadas**, evitando loops Python linha a linha.
  - Benefícios:
    - Maior **performance** em conjuntos de dados grandes (milhões de linhas);
    - Melhor uso de otimizações internas do NumPy/Pandas (C / SIMD).

- **Tratamento de dados sujos e edge cases**:
  - Timestamps inválidos são convertidos para `NaT` e removidos.
  - Timestamps no futuro são filtrados para evitar distorções nas métricas.
  - Linhas com `country` nulo são removidas antes das agregações.
  - Tipos de evento inválidos (`"???"`) são descartados.
  - Colisões de `event_id` são resolvidas por `drop_duplicates`, garantindo unicidade.

- **Modelagem de receita e anomalias**:
  - A escolha de uma distribuição **lognormal** para valores de `purchase`/`refund` simula melhor a cauda longa de preços em e‑commerce.
  - A detecção de anomalias baseada em **Z‑score** (3‑sigma) é simples, porém eficaz para destacar dias com receitas muito acima/abaixo da média.

- **Retenção baseada em coortes**:
  - A retenção D1 é calculada por **coortes de signup** usando joins vetorizados, o que escala bem para grandes volumes.

- **API com cache em memória por mtime**:
  - O cache evita recomputar o pipeline completo em cada requisição ao `/report`, usando o `mtime` do arquivo como gatilho de invalidação.
  - Simples, eficiente e adequado para um ambiente de **batch + leitura**.

---
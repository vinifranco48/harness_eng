# Harness Eng

Chat web para **Claude Code** e **Codex**, com a base de autenticação adaptada do
[Omnigent](https://github.com/omnigent-ai/omnigent). Converse com suas contas já
conectadas, escolha os modelos retornados pelos CLIs e acompanhe as respostas
conforme são geradas. A página **Contas** permite conectar, trocar e desconectar
contas e enviar o código de confirmação do Claude pelo navegador.

## Estado atual

| Área | Entrega atual |
| --- | --- |
| Autenticação e chat | Login nativo Claude/Codex, seleção de modelos, streaming, cancelamento e histórico de conversas |
| Interface | Conversas e Contas com identidade Insolo, menu lateral e temas claro/escuro |
| MariaDB | Conexão configurável, provisionamento administrativo, migração versionada e consulta de status |
| Análises - task 1 concluída | API para criar, listar e consultar análises persistidas no MariaDB |
| Dremio | Adapter REST para catálogo e consultas; integração com o fluxo analítico ainda pendente |
| Próximas etapas | Datasets, gráficos, tela de relatórios, ferramentas dos agentes e atualização dos resultados |

Uma análise é a base de um relatório salvo: hoje contém identificador, título
e datas. Ela existe independentemente de uma conversa. Consultas, gráficos,
edição, exclusão, ownership e versões publicadas serão acrescentados em outras
tasks. O login Claude/Codex autentica o provedor; ainda não há identidade própria
de usuários da aplicação.

## Estrutura da codebase

```text
apps/
└── web/
    ├── app/                   # Front: HTML, CSS, JavaScript e ícones
    └── api/                   # Back: Python e FastAPI
        ├── routes/            # Endpoints HTTP e validação das requisições
        ├── domain/            # Análises, provedores, estados e regras
        ├── adapter/           # Integrações e repositórios de persistência
        │   ├── dremio/        # Cliente REST do Dremio
        │   └── mysql/         # Conexão MariaDB e repositório de análises
        ├── services/          # Casos de uso de análises, autenticação e chat
        ├── dependencies.py    # Serviços por instância e proteção CSRF
        ├── middleware.py      # Cabeçalhos de segurança e hosts locais
        ├── main.py            # Composição da aplicação e ciclo de vida
        └── cli.py             # Inicialização web e comandos de terminal
main.py                        # Entrada pela raiz do projeto
harness_eng/                   # Compatibilidade com a entrada Python anterior
migrations/mysql/              # SQL versionado, aplicado administrativamente
scripts/                       # Provisionamento e migração por SSH
tests/                         # Testes unitários, HTTP e integração opt-in
```

As rotas recebem os serviços por dependências do FastAPI. Os tipos de domínio
ficam separados do transporte HTTP; os serviços integram os provedores nativos
e persistem as conversas. `api/main.py` monta as rotas e serve os arquivos de
`app/` em `/static`, com a conversa em `/` e as contas em `/connections`.

O backend usa FastAPI e o front usa HTML, CSS e JavaScript. As contas e o
histórico continuam nos mesmos diretórios locais. O pacote `harness_eng`
mantém os imports públicos anteriores e delega a implementação a `apps.web.api`.

## Interface Insolo

As telas de conversas e contas mantêm o logo Insolo em uma interface minimalista,
inspirada no Codex e no Claude: superfícies neutras, verde discreto nos estados
de conexão e tipografia de sistema. A conversa vazia centraliza o campo de
mensagem e as sugestões; com mensagens, o campo fica no rodapé. A navegação fica
em um menu lateral com histórico compacto. O botão do painel recolhe o menu para
uma faixa de ícones no desktop; no celular, abre uma gaveta. A preferência de
recolhimento fica salva no navegador, assim como o tema claro ou escuro,
alternado pelo botão na parte inferior do menu.

Os seletores de provedor, modelo e raciocínio ficam junto ao campo de mensagem.
As contas aparecem em uma lista com status e ações de conexão.

Os logos e os arquivos da fonte Nunito Sans estão em `apps/web/app/assets/`, servidos localmente.
A licença da fonte está em `assets/Nunito-Sans-OFL.txt`. Os estilos comuns e
controles de tema/menu ficam em `styles.css` e `ui.js`; cada página tem seu CSS.

## Banco da aplicação: MariaDB

A aplicação usa MariaDB para persistir seus metadados, com compatibilidade
validada na versão **10.3.39**. Os endereços `mysql.example.com` e
`servidor.example.com` usados abaixo são fictícios; substitua-os pelos endereços
do seu ambiente.

Nos exemplos, o banco é **harness_eng** e o usuário da aplicação é
**harness_app**, com permissões `SELECT`, `INSERT`, `UPDATE` e `DELETE` somente
em `harness_eng.*`. A autorização de conexão é vinculada ao endereço de origem
do cliente. O acesso SSH administrativo é usado para o provisionamento;
a aplicação usa suas próprias credenciais de banco.

A instância atual está com TLS desabilitado; a conexão usa TCP na rede interna.
Para comunicação por uma rede não confiável, será necessário habilitar TLS
ou usar um túnel SSH.

O provisionamento idempotente é executado pela raiz do projeto:

```powershell
uv run python -m scripts.provision_mysql --ssh-target root@servidor.example.com
```

Se a VPN apresentar um IP novo ao servidor, a autorização desse endereço exige
a opção administrativa explícita abaixo. O script identifica o IP pela sessão
SSH e confirma as credenciais contra uma conta existente antes de criar o
acesso; mantém as contas dos endereços anteriores.

```powershell
uv run python -m scripts.provision_mysql --ssh-target root@servidor.example.com --allow-current-host
```

A configuração local fica em `.harness-eng/mysql.json`, dentro da pasta já
ignorada pelo Git. Exemplo de formato, com senha de preenchimento:

```json
{
  "host": "mysql.example.com",
  "port": 3306,
  "database": "harness_eng",
  "user": "harness_app",
  "password": "PLACEHOLDER"
}
```

O adapter lê o arquivo diretamente, sem copiar credenciais para o ambiente do
processo. As variáveis `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`
e `MYSQL_PASSWORD`, quando definidas, substituem os campos correspondentes do
JSON. Credenciais do banco são removidas do ambiente entregue aos subprocessos
Claude e Codex.

Para confirmar a conexão, o banco selecionado e a versão do servidor:

```powershell
uv run harness-eng database status
uv run harness-eng database status --json
uv run harness-eng database status --config .harness-eng/mysql.json --json
```

Esses comandos retornam somente metadados da conexão, sem a senha. O backend
também expõe `GET /api/database/status`; configuração ausente ou conexão
indisponível retorna HTTP `503` com uma mensagem controlada.

Na aplicação web, o arquivo é procurado no mesmo diretório de dados do chat:
`.harness-eng/mysql.json` por padrão, ou `<auth-home>/web/mysql.json` ao usar
`--auth-home`. A criação da aplicação carrega a configuração; a consulta ao
banco acontece quando solicitada, sem conexão ou execução de DDL no startup.

O SQL da aplicação deve passar pelo adapter em
`apps/web/api/adapter/mysql/client.py`. Seu contexto `client.connection()` abre
uma conexão por operação, confirma a transação em caso de sucesso e executa
rollback em caso de erro, sempre fechando a conexão. O cliente configurado
fica disponível no backend pela dependência `MySQLService`.

As análises são persistidas no MariaDB. O histórico de conversas continua no
SQLite local; datasets, ferramentas e gráficos serão tratados nas próximas
tarefas.

## Análises persistentes — task 1

`Analysis` representa um relatório independente de qualquer conversa ou login
de provedor. Seu estado inicial contém `id` (UUID4), `title`, `created_at` e
`updated_at`. O título é aparado nas extremidades e deve conter de 1 a 120
caracteres. As datas são geradas em UTC, preservadas com microssegundos no
MariaDB e devolvidas em ISO 8601 com `Z`.

O fluxo segue `routes → services → domain + adapter/mysql`: o domínio valida
a análise, o serviço coordena os casos de uso e o repositório executa SQL
parametrizado. `AnalysisService` recebe um `AnalysisRepository` por injeção,
permitindo usar o mesmo serviço pelas futuras ferramentas do agente.

### Roteiro para entender o código

| Ordem | Arquivo | Responsabilidade |
| --- | --- | --- |
| 1 | [domain/analysis.py](apps/web/api/domain/analysis.py) | Entidade, regras do título, UUID4, datas UTC e erros do domínio |
| 2 | [services/analysis.py](apps/web/api/services/analysis.py) | Contrato `AnalysisRepository` e casos de uso `create`, `get` e `list` |
| 3 | [adapter/mysql/analysis_repository.py](apps/web/api/adapter/mysql/analysis_repository.py) | SQL parametrizado e reconstrução dos objetos vindos do banco |
| 4 | [adapter/mysql/client.py](apps/web/api/adapter/mysql/client.py) | Configuração, abertura da conexão, commit, rollback e fechamento |
| 5 | [routes/schemas.py](apps/web/api/routes/schemas.py) e [routes/analyses.py](apps/web/api/routes/analyses.py) | Formatos HTTP, endpoints e conversão dos erros em respostas |
| 6 | [dependencies.py](apps/web/api/dependencies.py) e [main.py](apps/web/api/main.py) | Composição dos componentes e injeção do serviço nas rotas |

Por exemplo, ao receber `POST /api/analyses` com
`{"title": "  Vendas por fazenda  "}`:

1. O schema HTTP exige um título textual e rejeita campos extras; a rota
   também verifica o token CSRF.
2. A dependência recupera o `AnalysisService` montado em `app.state.analysis`.
3. A rota aguarda `asyncio.to_thread(analysis.create, body.title)`, executando
   o trabalho síncrono fora do event loop.
4. `Analysis.create()` apara o título, valida de 1 a 120 caracteres e Unicode
   válido, gera UUID4 e usa o mesmo instante UTC para as duas datas.
5. O serviço solicita a inserção ao repositório; o repositório executa o SQL
   com parâmetros pela conexão do adapter.
6. O contexto da conexão confirma a transação e fecha o socket. Em erro,
   executa rollback. Após o sucesso, a API retorna `201` com a análise criada.

O caso de uso de criação é pequeno porque delega cada responsabilidade:

```python
def create(self, title: str) -> Analysis:
    analysis = Analysis.create(title)
    self.repository.insert(analysis)
    return analysis
```

O `Protocol` descreve as operações esperadas do repositório; a composição em
`main.py` escolhe `MySQLAnalysisRepository`. Nos testes, esse mesmo serviço
recebe um repositório em memória. Isso permite testar as regras sem abrir uma
conexão com o banco.

Dois detalhes de implementação: as regras de criação estão em
`Analysis.create()`; o construtor direto `Analysis(...)` é usado para reconstruir
os registros. Além disso, `DATETIME(6)` não armazena fuso horário: o adapter
escreve valores em UTC e recoloca essa informação na leitura. `public()`
serializa as datas com `Z`.

### Aplicar a migração

Execute pela raiz do projeto, usando o acesso administrativo SSH:

```powershell
uv run python -m scripts.migrate_mysql --ssh-target root@servidor.example.com --config .harness-eng/mysql.json
```

O comando aplica `migrations/mysql/001_create_analyses.sql`, cria a tabela
`analyses` e registra a versão/checksum em `schema_migrations`. Reexecuções
retornam `unchanged`; divergências de checksum ou schema interrompem a
operação. Um lock do MariaDB serializa as execuções administrativas.

DDL no MariaDB não é transacional. Se houver interrupção após criar a tabela,
a próxima execução só registra a migração quando o schema existente corresponde
ao esperado. A aplicação web não cria nem altera tabelas; `harness_app` mantém
somente permissões de CRUD sobre registros.

### API

| Método | Endpoint | Resultado |
| --- | --- | --- |
| `POST` | `/api/analyses` | Cria uma análise e retorna `201` com `{ "analysis": ... }` |
| `GET` | `/api/analyses?limit=20&offset=0` | Retorna `{ "analyses": [...], "limit": 20, "offset": 0 }` |
| `GET` | `/api/analyses/{id}` | Recupera a análise pelo UUID |

A listagem ordena por `updated_at DESC, id DESC`. `limit` aceita de 1 a 100;
`offset` aceita de 0 a 2147483647. UUID ou payload inválido retorna `422`,
análise ausente retorna `404` e armazenamento indisponível retorna `503` sem
detalhes internos. O corpo de criação aceita somente `title`; identificador e
datas são controlados pelo backend.

O `POST` exige o token CSRF da página. Quando informado, o cabeçalho `Origin`
é comparado com a origem do servidor.
Exemplo com o backend em execução:

```powershell
$baseUrl = 'http://localhost:8080'
$bootstrap = Invoke-RestMethod "$baseUrl/api/bootstrap"
$headers = @{ 'X-Harness-CSRF' = $bootstrap.csrf_token; 'Origin' = $baseUrl }
$payload = @{ title = 'Vendas por fazenda' } | ConvertTo-Json
$created = Invoke-RestMethod "$baseUrl/api/analyses" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($payload))
Invoke-RestMethod "$baseUrl/api/analyses/$($created.analysis.id)"
Invoke-RestMethod "$baseUrl/api/analyses?limit=20&offset=0"
```

A página `/analyses` reúne as análises e fontes importadas. O Codex pode criar
análises e gráficos usando as ferramentas da aplicação descritas abaixo.

### Ferramenta de criação de análises

O nome público da ferramenta é `analysis_create`. Seu contrato JSON é gerado
por `AnalysisCreateArgs`, usando as mesmas regras que validam a execução:
título textual de 1 a 120 caracteres, remoção de espaços nas extremidades e
rejeição de campos extras.

O registro fica em `apps/web/api/tools/registry.py`. Na inicialização,
`create_app()` monta `app.state.tool_executor` e `app.state.tool_definitions`
com o mesmo `AnalysisService` usado pela API. Com o MariaDB configurado, o
fluxo é `ToolExecutor → AnalysisCreateTool → AnalysisService → repositório`.
Sem configuração de banco, a lista de ferramentas fica vazia e o chat
continua disponível.

Para reutilizar o registro com um serviço já construído em Python:

```python
from apps.web.api.tools.registry import create_tool_executor

executor = create_tool_executor(analysis_service)
result = executor.execute("analysis_create", {"title": "Receita Comercial"})
```

O sucesso retorna `ok`, `analysis_id` e `title`. Argumentos inválidos retornam
`INVALID_ARGUMENTS`; falhas de persistência retornam `STORAGE_ERROR`, sem
detalhes internos; nomes não registrados retornam `TOOL_NOT_FOUND`.
O `ChatManager` recebe o executor e as definições. O Codex anuncia as ferramentas
em `thread/start` com `experimentalApi`; `CodexToolBridge` traduz chamadas
`item/tool/call` durante o RPC e o streaming. Outras solicitações permanecem
bloqueadas. A sessão verifica thread/turn e evita repetir um `callId` durante
a mesma execução. Uma mudança nas definições inicia uma thread compatível e
reapresenta o histórico textual recente da conversa.

### Criar gráficos a partir de uma planilha

Para experimentar sem um servidor MariaDB:

```bash
uv run harness-eng serve --port 8080 --analysis-storage sqlite
```

Abra `http://localhost:8080/analyses`, importe um CSV ou Excel e escolha
**Analisar com Codex**. Também é possível usar **Dados de exemplo**, criar uma
análise e adicionar gráficos pelo formulário. No chat, experimente:

> Use os dados de exemplo e crie uma análise de vendas de 2025, com receita
> por região em barras e receita mensal por produto em linhas. Salve os
> gráficos e retorne o link da análise.

O modo `sqlite` é explícito e grava análises, fontes e gráficos em
`.harness-eng/analytics.sqlite3`. O modo padrão `mysql` mantém análises no
MariaDB configurado; fontes e gráficos ficam nesse arquivo SQLite local.
Ao usar `--auth-home`, o arquivo fica na pasta `web` desse diretório.
Reiniciar preserva os dados. Não há migração automática entre os modos.

| Tool | Função |
| --- | --- |
| `dataset_import_demo` | Disponibiliza as 144 linhas fictícias de vendas de 2025 |
| `dataset_list` | Lista fontes e tipos das colunas |
| `dataset_get` | Retorna perfil e prévia de até 20 linhas |
| `analysis_create` | Salva uma análise e retorna seu link |
| `analysis_list` / `analysis_get` | Consulta análises e seus gráficos |
| `chart_create` | Valida colunas e salva um gráfico Vega-Lite |

As tools do chat estão disponíveis no Codex. O Claude continua com suas
ferramentas desabilitadas; os formulários de análises independem do provedor.

A importação aceita UTF-8 em CSV (vírgula, ponto e vírgula ou tabulação) e a
primeira aba visível de XLSX. Limites: 5 MiB, 5.000 linhas e 50 colunas.
O cabeçalho deve estar na primeira linha, com nomes únicos. Datas ISO e células
de data do Excel são normalizadas em UTC; datas sem fuso são interpretadas em
UTC. Vazios são preservados como ausentes, e zeros continuam sendo zeros.
Fórmulas do Excel precisam de valores calculados salvos; o servidor não as
executa. Importações idênticas reutilizam a mesma fonte.

`chart_create` recebe `analysis_id`, `dataset_id`, `title`, `mark`, `x`, `y`,
`aggregate` e `color` opcional. Os tipos são `bar`, `line`, `area` e `point`;
os cálculos são `sum`, `mean`, `count`, `min` e `max`. Contagem usa registros
com X e Y presentes. Linha/área exigem X numérico ou temporal. Os gráficos
guardam uma cópia dos dados usados, não executam SQL nem carregam URLs externas.

Os bundles locais fixados são Vega 6.4.0, Vega-Lite 6.5.0 e Vega-Embed 7.3.0,
com versões, integridade e licenças em `apps/web/app/vendor`. A renderização
SVG usa o [intérprete compatível com CSP](https://vega.github.io/vega/usage/#vega-and-content-security-policy),
sem liberar `unsafe-eval`. É possível baixar cada gráfico em SVG.
`uv run python scripts/vendor_vega.py` reproduz os bundles verificados.

As fixtures estão em `apps/web/api/data/vendas_demo.csv` e
`vendas_demo_totals.json`: receita R$ 5.174.501,20, custo R$ 2.868.766,21 e
13.452 unidades. São dados fictícios, conferidos também por região, mês e
produto. `node scripts/build_demo_workbook.mjs --data-only` reproduz as fixtures;
sem esse argumento, o builder usa o runtime externo `@oai/artifact-tool` para
gerar `outputs/harness-analytics-demo/vendas_demo.xlsx`.

| Endpoint | Uso |
| --- | --- |
| `GET /api/datasets` | Catálogo de fontes |
| `POST /api/datasets` | Upload multipart no campo `file`, nome opcional em `name` |
| `POST /api/datasets/demo` | Importação dos dados fictícios |
| `GET /api/datasets/{id}` | Perfil e prévia |
| `GET/POST /api/analyses/{id}/charts` | Lista/cria gráficos da análise |
| `GET /api/charts/{id}` | Recupera o gráfico e sua especificação |

As operações de escrita usam a mesma proteção de origem e CSRF do restante
da API. Os testes `test_analytics.py`, `test_analytics_api.py`,
`test_chat_tools.py` e `test_vega_runtime.py` cobrem dados, persistência,
protocolo e agregações no Vega real (Node.js necessário para este último).

## Conexão com o Dremio

O cliente em `apps/web/api/adapter/dremio/client.py` conecta ao Dremio Software
pela REST API v3. Configure a URL da instância e um token pessoal ou OAuth pelas
variáveis de ambiente `DREMIO_BASE_URL` e `DREMIO_ACCESS_TOKEN`. O token é
enviado no cabeçalho Bearer e fica fora do código e dos logs.

O adapter ainda não está composto nos serviços da aplicação nem disponível
como ferramenta para o chat. A integração analítica será uma próxima task.

No PowerShell, informe as variáveis na sessão que executa o Harness Eng:

```powershell
$env:DREMIO_BASE_URL = 'https://dremio.example.com'
$env:DREMIO_ACCESS_TOKEN = 'seu-token-dremio'
```

O cliente lê o catálogo para confirmar a conexão e também pode submeter SQL,
aguardar o job e buscar uma página com até 500 linhas:

```python
from apps.web.api.adapter.dremio import DremioClient

with DremioClient.from_env() as dremio:
    print(dremio.check_connection())
    result = dremio.query('SELECT 1', limit=100)
```

`limit` controla a página de resultados retornada. Não limita o custo de
execução da consulta nem estabelece uma política de SQL somente leitura.

O endereço deve ser da instância Dremio Software, sem precisar incluir
`/api/v3`. A operação de leitura do catálogo usa a [REST API do Dremio](https://docs.dremio.com/current/reference/api/catalog/);
consultas usam os endpoints de [SQL](https://docs.dremio.com/current/reference/api/sql/), [jobs](https://docs.dremio.com/current/reference/api/job/) e resultados.

## Executar

Para executar o backend: Python 3.11+, `uv` e as dependências do projeto.
Para conversar com um provedor, instale também seu CLI (`claude` ou `codex`).
A API de análises usa o MariaDB configurado com a migração aplicada e funciona
independentemente desses CLIs.

```powershell
cd C:\Users\vinicius.franco\Documents\projetos_insolo\harness_eng
uv sync
uv run harness-eng
```

Abra **http://localhost:8080**. O servidor também pode ser iniciado com:

```powershell
uv run main.py
uv run harness-eng serve --port 8080
uv run python -m apps.web.api serve --port 8080
uv run harness-eng serve --auth-home .harness-eng/auth
```

## Conversar no navegador

1. Selecione **Claude** ou **Codex** junto ao campo de mensagem.
2. Escolha um modelo. O seletor consulta a inicialização do Claude e
   `model/list` do Codex App Server. As opções vêm do CLI instalado; não são
   uma lista fixa e o acesso efetivo é confirmado pelo provedor ao responder.
3. Para Codex, escolha também o nível de **Raciocínio** retornado pelo catálogo.
4. Escreva e pressione **Enter** ou **Enviar**. **Shift + Enter** cria uma nova
   linha. **Parar** interrompe a resposta em andamento.
5. Troque de provedor diretamente no seletor. Ao trocar, a interface abre uma
   nova conversa e mantém a anterior no histórico. **Nova conversa** também
   inicia uma conversa vazia. O modelo pode ser alterado entre mensagens do
   mesmo provedor, mantendo a conversa nativa.

O Claude usa o protocolo `stream-json` do CLI com `--resume`. O Codex usa
`thread/start`, `thread/resume`, `turn/start` e os eventos do App Server.
Os CLIs oficiais gerenciam autenticação e renovação de credenciais; nenhuma
chave de API precisa ser informada ao chat.

O histórico do Harness Eng fica em `.harness-eng/chats.sqlite3`, com salvamento
parcial durante a resposta. As sessões nativas ficam nos diretórios dos próprios
CLIs. Excluir uma conversa remove seu histórico do Harness Eng. Reabrir a
página recupera o histórico e acompanha uma resposta que ainda esteja rodando.
Reiniciar o servidor interrompe as respostas em andamento e conserva o texto
já recebido. O modo `--auth-home` também separa o banco de conversas.

O chat apresenta texto, listas, links e blocos de código com botão de cópia.
O Claude mantém ferramentas desabilitadas. O Codex usa sandbox de leitura e
executa somente as tools analíticas registradas na aplicação, sem operar
arquivos ou comandos do computador.

## Gerenciar contas

O servidor roda nesta máquina e usa os CLIs instalados nela. O login do Codex
pelo navegador usa `codex app-server` com `account/login/start`; a opção de
código de dispositivo usa o comando nativo `codex login --device-auth`.
O login do Claude usa `claude auth login --claudeai` com entrada e saída
conectadas à aplicação web. Quando houver um código de confirmação, cole-o
na janela de login da própria aplicação.

As operações rodam em segundo plano. A página acompanha conclusão, falha,
expiração e cancelamento. Fechar uma janela de login pendente cancela essa
operação. A confirmação de sucesso depende do status retornado pelo provedor.

O aplicativo é uma interface web local, acessível por `localhost`. Ele gerencia
as contas desta máquina; não é um serviço de contas para vários usuários.
URLs de autorização e códigos de dispositivo existem apenas durante o login;
tokens e a saída bruta dos processos não são retornados à interface.
As alterações exigem um token da página e verificam sua origem.

O CLI continua disponível para automações:

Comandos disponíveis:

```powershell
uv run harness-eng login claude
uv run harness-eng login codex
uv run harness-eng login codex --device-auth
uv run harness-eng status
uv run harness-eng status claude --json
uv run harness-eng logout codex
```

O login abre o fluxo do CLI correspondente. Conclua a autenticação no navegador
ou siga as instruções de código de dispositivo no terminal. O resultado é
confirmado consultando o CLI depois do login. Se já existe um login válido, a
operação reutiliza essa autenticação; `--force` inicia um novo fluxo.

Sem argumentos, o comando inicia a aplicação web. `status` consulta os provedores;
`setup` abre o menu de terminal.
Os códigos de saída são `0` para sucesso, `1` para falha ou status desconectado,
`2` para argumentos inválidos e `130` para interrupção por Ctrl+C.

## Onde ficam os logins

Por padrão, as operações usam as contas salvas pelos CLIs oficiais. Um logout
nesse modo também desconecta a conta usada pelo CLI correspondente no terminal.
`CODEX_HOME` e `CLAUDE_CONFIG_DIR` existentes são respeitados.

Para manter contas separadas para este projeto, passe o mesmo `--auth-home` em
todas as operações:

```powershell
uv run harness-eng login claude --auth-home .harness-eng/auth
uv run harness-eng login codex --auth-home .harness-eng/auth
uv run harness-eng status --auth-home .harness-eng/auth
uv run harness-eng logout codex --auth-home .harness-eng/auth
```

Os próprios CLIs gerenciam e renovam suas credenciais. A pasta `.harness-eng/`
está no `.gitignore`. As variáveis de chave de API, token e gateway são removidas
**somente do ambiente dos subprocessos** para consultar e operar o login salvo;
o ambiente do terminal permanece como estava.

O status é uma consulta local de autenticação: não envia prompts, não verifica
limites da assinatura e não confirma acesso a um modelo. O JSON expõe apenas
estado, método de autenticação, caminho do CLI e mensagens controladas.

## Usar no harness Python

```python
from apps.web.api.domain.auth import Provider
from apps.web.api.services.auth import AuthManager

auth = AuthManager()  # opcional: auth_home=".harness-eng/auth"
status = auth.status(Provider.CODEX)

if not status.connected:
    result = auth.login(Provider.CODEX)
    if not result.success:
        raise RuntimeError(result.message)

environment = auth.environment(Provider.CODEX)
# Passe environment para env= ao iniciar o CLI no seu harness.
```

`AuthManager.login()` herda o terminal para que URLs, códigos e prompts cheguem
ao usuário. Use-o em um terminal interativo. `auth.environment(provedor)`
retorna uma cópia do ambiente com o mesmo home usado no login e sem overrides
de chave de API/gateway. Passe essa cópia ao processo do harness para que ele
use a mesma autenticação.

## Resolução dos CLIs no Windows

O pacote encontra os CLIs no `PATH`, em `%APPDATA%\npm` e em
`%USERPROFILE%\.local\bin`. Para instalações npm, resolve o executável nativo
por trás dos wrappers `.cmd`/`.ps1`, inclusive caminhos com espaços.

Para uma instalação em outra pasta:

```powershell
$env:HARNESS_ENG_CLAUDE_BIN = 'C:\ferramentas\claude.exe'
$env:HARNESS_ENG_CODEX_BIN = 'C:\ferramentas\codex.exe'
```

Também aceita um entrypoint `.js` quando Node.js está disponível no `PATH`.
Um override inválido retorna erro em vez de executar outra instalação.
`--timeout` configura o prazo da operação em segundos; o padrão de login é 600.

## Validação

### Resultado da task 1

Na última execução completa registrada, **121 testes passaram**, incluindo
os quatro testes de integração no MariaDB real. Foram confirmados:

- Criação, leitura e listagem de análises com UUID e datas preservados.
- Recuperação da mesma análise após encerrar e iniciar outro processo HTTP.
- Persistência de Unicode, rollback e rejeição de inserção com UUID duplicado.
- Migração aplicada e reexecução idempotente com resultado `unchanged`.
- Remoção dos registros criados pelos testes ao final da execução.

### Como reproduzir

```powershell
uv run python -m unittest discover -s tests -v
```

Os testes padrão usam processos simulados e diretórios temporários para verificar
erros de autenticação, confirmação de login/logout, isolamento de ambiente,
resolução no Windows, API web, envio de código, protocolo do App Server e
cancelamento de processos. Também cobrem domínio/serviço de análises, API,
paginação, CSRF, SQL parametrizado, UTC, transações MySQL e o runner de migrações.
Não executam login/logout nas contas reais nem acessam o banco remoto.

Para executar também os testes de integração com o MariaDB configurado,
aplique a migração e habilite explicitamente:

```powershell
$env:HARNESS_MYSQL_INTEGRATION = '1'
uv run python -m unittest discover -s tests -v
Remove-Item Env:\HARNESS_MYSQL_INTEGRATION
```

Esses testes usam `.harness-eng/mysql.json` (ou o caminho em
`HARNESS_MYSQL_CONFIG`), verificam persistência entre conexões, Unicode,
rollback e inserção duplicada. Um servidor HTTP de teste em porta temporária
é encerrado e iniciado novamente para confirmar a recuperação do mesmo UUID e
datas. Os registros criados são removidos por seus próprios UUIDs ao final;
o histórico de chat usa um diretório temporário.

| Arquivo | Cobertura |
| --- | --- |
| [test_analysis.py](tests/test_analysis.py) | Domínio e casos de uso com repositório em memória |
| [test_analyses_api.py](tests/test_analyses_api.py) | HTTP, validação, paginação, CSRF e indisponibilidade do banco |
| [test_analysis_repository.py](tests/test_analysis_repository.py) | SQL parametrizado, datas UTC e falhas do repositório |
| [test_mysql_client.py](tests/test_mysql_client.py) | Configuração, commit, rollback e fechamento da conexão |
| [test_mysql_migrations.py](tests/test_mysql_migrations.py) | Lock, checksum, compatibilidade MariaDB e erros de migração |
| [test_mysql_provision.py](tests/test_mysql_provision.py) | Provisionamento e autorização explícita do IP atual |
| [test_analysis_mysql_integration.py](tests/test_analysis_mysql_integration.py) | Banco real e reinício do backend |

## Referência utilizada

Fluxo adaptado de
[`omnigent/onboarding/harness_install.py`](https://github.com/omnigent-ai/omnigent/blob/f6b30fcc984ff62ab2847c7d0c96fdc417d34fdc/omnigent/onboarding/harness_install.py)
no commit `f6b30fcc984ff62ab2847c7d0c96fdc417d34fdc`: comandos nativos de login,
status e logout, reutilização de login existente e verificação após a operação.
Esta adaptação adiciona resolução Windows e validação estrita do status JSON.

A licença Apache 2.0 da referência está em `third_party/omnigent/LICENSE`.
Consulte também `NOTICE`.

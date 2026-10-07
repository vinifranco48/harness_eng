# Harness Eng

Chat web para **Claude Code** e **Codex**, com a base de autenticação adaptada do
[Omnigent](https://github.com/omnigent-ai/omnigent). Converse com suas contas já
conectadas, escolha os modelos retornados pelos CLIs e acompanhe as respostas
conforme são geradas. A página **Contas** permite conectar, trocar e desconectar
contas e enviar o código de confirmação do Claude pelo navegador.

## Estrutura da codebase

```text
apps/
└── web/
    ├── app/                   # Front: HTML, CSS, JavaScript e ícones
    └── api/                   # Back: Python e FastAPI
        ├── routes/            # Endpoints HTTP e validação das requisições
        ├── domain/            # Provedores, estados, resultados e erros
        ├── adapter/           # Integrações HTTP com sistemas externos
        │   └── dremio/        # Cliente REST do Dremio
        ├── services/          # Autenticação, CLIs, modelos, chat e SQLite
        ├── dependencies.py    # Serviços por instância e proteção CSRF
        ├── middleware.py      # Cabeçalhos de segurança e hosts locais
        ├── main.py            # Composição da aplicação e ciclo de vida
        └── cli.py             # Inicialização web e comandos de terminal
main.py                        # Entrada pela raiz do projeto
harness_eng/                   # Compatibilidade com a entrada Python anterior
tests/                         # Testes existentes
```

As rotas recebem os serviços por dependências do FastAPI. Os tipos de domínio
ficam separados do transporte HTTP; os serviços integram os provedores nativos
e persistem as conversas. `api/main.py` monta as rotas e serve os arquivos de
`app/` em `/static`, com a conversa em `/` e as contas em `/connections`.

O backend usa FastAPI e o front usa HTML, CSS e JavaScript. As contas e o
histórico continuam nos mesmos diretórios locais. O pacote `harness_eng`
mantém os imports públicos anteriores e delega a implementação a `apps.web.api`.

## Interface Insolo

As telas de conversas e contas seguem a identidade do portal `bi_test_2`:
logo Insolo, fonte Nunito Sans e verde nos estados ativos. A navegação fica em
um menu lateral com histórico compacto. O botão do painel recolhe o menu para
uma faixa de ícones no desktop; no celular, abre uma gaveta. A preferência de
recolhimento fica salva no navegador, assim como o tema claro ou escuro,
alternado pelo botão na parte inferior do menu.

A organização visual usa como referências o [OpenCode Web](https://opencode.ai/docs/web/)
e o [Claude Code Desktop](https://claude.com/resources/articles/claude-code-desktop-redesign).
Os seletores de provedor, modelo e raciocínio ficam junto ao campo de mensagem.

Os logos e a fonte estão em `apps/web/app/assets/`, servidos localmente.
A licença da fonte está em `assets/Nunito-Sans-OFL.txt`. Os estilos comuns e
controles de tema/menu ficam em `styles.css` e `ui.js`; cada página tem seu CSS.

## Conexão com o Dremio

O cliente em `apps/web/api/adapter/dremio/client.py` conecta ao Dremio Software
pela REST API v3. Configure a URL da instância e um token pessoal ou OAuth pelas
variáveis de ambiente `DREMIO_BASE_URL` e `DREMIO_ACCESS_TOKEN`. O token é
enviado no cabeçalho Bearer e fica fora do código e dos logs.

No PowerShell, informe as variáveis na sessão que executa o Harness Eng:

```powershell
$env:DREMIO_BASE_URL = 'https://dremio.seu-dominio'
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

O endereço deve ser da instância Dremio Software, sem precisar incluir
`/api/v3`. A operação de leitura do catálogo usa a [REST API do Dremio](https://docs.dremio.com/current/reference/api/catalog/);
consultas usam os endpoints de [SQL](https://docs.dremio.com/current/reference/api/sql/), [jobs](https://docs.dremio.com/current/reference/api/job/) e resultados.

## Executar

Pré-requisitos: Python 3.11+ e os CLIs `claude` e `codex` instalados.

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
As conversas usam ferramentas desabilitadas no Claude e sandbox de leitura no
Codex, com instruções para responder diretamente sem operar arquivos locais.

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

O aplicativo é uma interface web local, servida em `127.0.0.1`. Ele gerencia
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

```powershell
uv run python -m unittest discover -s tests -v
```

Os testes usam processos simulados e diretórios temporários para verificar
erros de autenticação, confirmação de login/logout, isolamento de ambiente,
resolução no Windows, API web, envio de código, protocolo do App Server e
cancelamento de processos. Não executam login/logout nas contas reais.

## Referência utilizada

Fluxo adaptado de
[`omnigent/onboarding/harness_install.py`](https://github.com/omnigent-ai/omnigent/blob/f6b30fcc984ff62ab2847c7d0c96fdc417d34fdc/omnigent/onboarding/harness_install.py)
no commit `f6b30fcc984ff62ab2847c7d0c96fdc417d34fdc`: comandos nativos de login,
status e logout, reutilização de login existente e verificação após a operação.
Esta adaptação adiciona resolução Windows e validação estrita do status JSON.

A licença Apache 2.0 da referência está em `third_party/omnigent/LICENSE`.
Consulte também `NOTICE`.

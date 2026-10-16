# 🖨️ Geduc Printer - Sistema de Impressão Escolar

> **E.E. Antônio Reginato — Marília/SP**  
> Automação de reprografia e fila de impressão com imposição de livreto (2-up duplex) para a multifuncional **Brother MFC-L8610CDW**.

---

## 📌 Visão Geral da Arquitetura

O sistema integra três pontas principais:

```
[ Professores / Gestão ]
          │ (Envia pedidos e anexos PDF via Google Formulários / Planilha)
          ▼
[ Google Apps Script (Nuvem) ]
          │ (Fila de solicitações: Status "Liberado", "Concluído", etc.)
          ▼
[ Agente Python Local (Reprografia) ]
  ├── Consulta a nuvem a cada 15 segundos
  ├── Baixa os PDFs do Google Drive (trata confirmação de arquivos > 5MB)
  ├── Imposição matemática de páginas (Gera Livreto A4 Paisagem dobrável)
  ├── Encapsula comandos de hardware PJL (Duplex, Cópias, Modo P&B/Cor)
  └── Despacha para a impressora (USB RAW / Rede TCP 9100 / Simulador Virtual)
```

---

## ⚙️ Pré-requisitos

1. **Python 3.10+** (recomendado Python 3.12+ ou 3.14+)
2. **Poetry** (gerenciador de dependências e ambientes virtuais Python)
   - Instalação rápida do Poetry (se ainda não tiver):
     ```powershell
     (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | py -
     ```

---

## 📦 Instalação do Projeto

No terminal (Prompt de Comando ou PowerShell), na raiz do repositório:

```powershell
# 1. Instalar as dependências do projeto no ambiente virtual gerenciado pelo Poetry:
poetry install
```

As dependências instaladas incluem:
- `pypdf`: manipulação de PDFs, rotação e imposição de livretos (2-up).
- `pywin32`: comunicação direta com o subsistema Spooler do Windows (RAW/USB).

---

## 🛠️ Configuração Inicial

Abra o arquivo [`agent/agente_fila_impressao.py`](agent/agente_fila_impressao.py) e localize a seção `CONFIGURAÇÕES DA ESCOLA`:

```python
# 1. URL pública do Web App gerada no Google Apps Script (/exec):
WEBAPP_URL = "SUA_URL_DO_APPS_SCRIPT_AQUI"

# 2. Conexão com a impressora:
# Opções: "USB" (cabo direto), "REDE" (Ethernet/Wi-Fi) ou "SIMULADOR" (testes)
TIPO_CONEXAO = "USB"

# [Se USB]: "AUTO" detecta a Brother automaticamente, ou informe o nome exato
PRINTER_USB_NAME = "AUTO"

# [Se REDE]:
PRINTER_IP = "192.168.1.100"
PRINTER_PORT = 9100

# 3. Intervalo de checagem da fila em segundos:
INTERVALO_SEGUNDOS = 15

# 4. Modo de Simulação / Testes (dispensa impressora física):
MODO_SIMULADOR = False
```

---

## 🚀 Como Rodar

### 🟢 1. Ambiente de Produção (com Impressora Física Brother)

Certifique-se de que a impressora está ligada e conectada (via USB ou na mesma rede local).

#### Opção A: Pelo arquivo executável (.bat)
Dê um duplo clique no arquivo:
```
agent\iniciar_agente.bat
```

#### Opção B: Pelo terminal
```powershell
# Modo Sentinela (Loop contínuo verificando a nuvem a cada 15 segundos):
poetry run python agent/agente_fila_impressao.py --loop

# Modo Pontual (Verifica a fila uma única vez e finaliza):
poetry run python agent/agente_fila_impressao.py
```

---

### 🧪 2. Ambiente de Teste / Desenvolvimento (sem Impressora Física)

Ideal para desenvolvimento em máquinas locais (Windows ou Ubuntu/Linux) sem a multifuncional Brother física conectada. 

O simulador emula fielmente o subsistema do Spooler Windows (`win32print`), recebe os comandos PJL, **extrai o PDF limpo gerado** na pasta `agent/simulador_saida/` e permite simular falhas físicas em tempo real (falta de papel, atolamento, tampa aberta, etc.).

#### Passo 1: Iniciar o Painel do Simulador
Abra um terminal ou clique duas vezes em:
```cmd
.\agent\iniciar_simulador.bat
```
*(Ou pelo terminal: `poetry run python agent/simulador_brother.py`)*

#### Passo 2: Iniciar o Agente em Modo Simulador
Abra um segundo terminal ou clique duas vezes em:
```cmd
.\agent\iniciar_agente_simulado.bat
```
*(Ou pelo terminal: `poetry run python agent/agente_fila_impressao.py --loop --simulador`)*

#### Comandos Interativos do Painel do Simulador:
Com o simulador aberto, digite no terminal para controlar os estados de hardware:
- `1` : **Pronta / Ociosa** (Limpa qualquer erro ativo - `0x00000000`)
- `2` : **Injetar Sem Papel** (Status `0x00000020`)
- `3` : **Injetar Papel Preso / Atolamento** (Status `0x00000008`)
- `4` : **Injetar Tampa Aberta** (Status `0x00400000`)
- `5` : **Injetar Desconectada / Sem Energia** (Status `0x00000080`)
- `6` : **Abrir pasta de saída** (`agent/simulador_saida/`) no Windows Explorer para abrir os PDFs gerados
- `s` : Exibir bitmask de status e total de páginas contabilizadas pelo hardware
- `q` : Sair do simulador

---

## 📂 Estrutura do Repositório

```text
geduc-printer/
├── agent/
│   ├── agente_fila_impressao.py   # Script principal do Agente de Impressão
│   ├── simulador_brother.py       # Emulador de hardware da Brother MFC-L8610CDW
│   ├── iniciar_agente.bat         # Inicia o agente em loop (Produção)
│   ├── iniciar_agente_simulado.bat# Inicia o agente em loop (Modo Simulação)
│   ├── iniciar_simulador.bat      # Abre o painel interativo do simulador
│   ├── simulador_saida/           # PDFs e streams PJL (.prn) extraídos no teste
│   ├── requirements.txt           # Dependências para referência legada
│   └── legacy/                    # Versões anteriores e scripts de referência
├── web/
│   └── backend_apps_script.js     # Código do Web App do Google Apps Script
├── docs/                          # Manuais, roteiros de implantação e diagramas
├── pyproject.toml                 # Gerenciamento de projeto e dependências (Poetry)
├── poetry.lock                    # Versões exatas e hashes das dependências
└── README.md                      # Este guia de documentação
```

---

## ❓ Solução de Problemas

1. **Erro: `O arquivo está com acesso restrito no Google Drive`**
   - **Causa:** O PDF enviado pelo formulário/professor foi salvo em uma pasta do Google Drive com restrição de acesso.
   - **Solução:** No Google Drive, acesse a pasta raiz onde os uploads são armazenados, clique em *Compartilhar* e configure o *Acesso Geral* para *"Qualquer pessoa com o link"* (como *Leitor*).

2. **Erro: `A biblioteca 'pywin32' é obrigatória no Windows para conexão via USB`**
   - **Solução:** Execute `poetry install` ou certifique-se de iniciar os scripts com o ambiente virtual do Poetry ativado (`poetry run ...`).

3. **Como alternar rapidamente entre impressora real e simulador:**
   - Adicione o parâmetro `--simulador` ou altere `MODO_SIMULADOR = True` no topo de [`agente_fila_impressao.py`](agent/agente_fila_impressao.py).

# Guia: Subindo GridSense Simulator no GitHub

Este guia explica **exatamente** o que você precisa fazer para colocar
o projeto na sua conta GitHub, em passos simples.

## Pré-requisitos

1. **Conta no GitHub** (gratuita em https://github.com)
2. **Git instalado** na sua máquina
   - Windows: https://git-scm.com/download/win
   - Mac: `brew install git` ou download em https://git-scm.com
   - Linux: `apt install git` (Debian/Ubuntu) ou equivalente
3. **Ter feito download** do arquivo `gridsense-simulator-complete.zip`
4. **Ter extraído** a pasta `gridsense-simulator`

## Verificar se git está instalado

Abra um terminal/PowerShell e rode:

```bash
git --version
```

Deve aparecer algo como: `git version 2.40.0` ou similar. Se não aparecer,
instale git primeiro.

## Passo 1: Criar um repositório vazio no GitHub

1. Acesse https://github.com/new (ou clique em "+" no canto superior
   direito do GitHub e selecione "New repository")
2. Preencha:
   - **Repository name:** `gridsense-simulator`
   - **Description:** "Electrical Grid Digital Twin — Phase 1 & 2"
   - **Visibility:** Public (recomendado para portfólio)
3. **IMPORTANTE:** NÃO marque nenhuma das opções:
   - ❌ Add a README file
   - ❌ Add .gitignore
   - ❌ Choose a license
   
   (Já temos tudo isso no projeto — vamos apenas fazer upload)

4. Clique em "Create repository"

Você verá uma página com instruções. Guarde a URL que aparece, tipo:
```
https://github.com/SEU_USUARIO/gridsense-simulator.git
```

## Passo 2: Abrir terminal na pasta do projeto

### Windows (PowerShell)
1. Abra o File Explorer
2. Navegue até a pasta `gridsense-simulator` extraída
3. Clique na barra de endereço e escreva `powershell`
4. Pressione Enter — um terminal PowerShell abre naquela pasta

### Mac / Linux
1. Abra um terminal
2. Navegue até a pasta:
   ```bash
   cd ~/Downloads/gridsense-simulator
   # ou onde você extraiu
   ```

## Passo 3: Executar o script de inicialização (MAIS FÁCIL)

A forma mais simples é usar o script que já preparei:

```bash
bash scripts/init_github.sh SEU_USUARIO_GITHUB gridsense-simulator
```

Exemplo:
```bash
bash scripts/init_github.sh luizgarcia gridsense-simulator
```

O script faz tudo automaticamente:
- ✓ Inicializa git
- ✓ Adiciona todos os arquivos
- ✓ Faz commit inicial
- ✓ Conecta ao seu GitHub
- ✓ Faz push (upload)

**Se funcionar,** você verá no final:

```
════════════════════════════════════════════════════════════
✓ Repository initialized and pushed to GitHub!
════════════════════════════════════════════════════════════

Next steps:
  1. Verify on GitHub:
     https://github.com/SEU_USUARIO/gridsense-simulator
```

Abra esse link no navegador — tudo deverá estar lá!

## Passo 3b: Fazer manualmente (se o script não funcionar)

Se você preferir fazer passo a passo, ou o script não funcionar:

```bash
# Iniciar git nessa pasta
git init

# Adicionar todos os arquivos
git add .

# Fazer commit inicial
git commit -m "Initial commit: GridSense Simulator Phase 1 & 2

- Simulation engine (pandapower)
- Kafka publisher
- Bronze consumer
- Zero-cost local stack"

# Adicionar o repositório remoto (substitua SEU_USUARIO)
git remote add origin https://github.com/SEU_USUARIO/gridsense-simulator.git

# Renomear branch para 'main' (padrão do GitHub)
git branch -M main

# Fazer push (enviar pro GitHub)
git push -u origin main
```

Se pedir login:
- **HTTPS:** use um Personal Access Token (gerado em https://github.com/settings/tokens)
- **SSH:** use sua chave SSH (já configurada, provavelmente)

## Passo 4: Verificar no navegador

Acesse:
```
https://github.com/SEU_USUARIO/gridsense-simulator
```

Você deve ver:
- ✓ Todos os arquivos (`simulator/`, `ingestion/`, `Makefile`, etc.)
- ✓ O commit message no histórico
- ✓ README.md renderizado

Se aparecer tudo, 🎉 **sucesso!**

## Troubleshooting

### Erro: "fatal: not a git repository"

Certifique-se de que você está DENTRO da pasta:
```bash
cd gridsense-simulator
pwd  # mostra o caminho atual
```

### Erro: "Permission denied (publickey)" ou similar

GitHub não aceita mais password direto. Você precisa:

**Opção 1: Personal Access Token (mais fácil para começar)**
1. Vá para https://github.com/settings/tokens
2. Clique "Generate new token (classic)"
3. Marque `repo` (acesso a repositórios)
4. Copie o token (vai aparecer uma vez)
5. Quando git pedir senha, use o token

**Opção 2: SSH (mais seguro, profissional)**
Segue https://docs.github.com/en/authentication/connecting-to-github-with-ssh

### Erro: "already exists"

Se aparecer algo como `fatal: remote origin already exists`:

```bash
git remote remove origin
git remote add origin https://github.com/SEU_USUARIO/gridsense-simulator.git
```

### Erro: "Updates were rejected" ou "non-fast-forward"

Provavelmente você clicou "Initialize with README" no GitHub. Nesse caso:

```bash
git pull origin main --allow-unrelated-histories
git push -u origin main
```

## Próximos passos

Depois de fazer upload:

1. **Testar Phase 2 localmente** (veja `README.md` do projeto)
   ```bash
   make up              # sobe Kafka
   make produce          # simulador envia telemetria
   make consume-bronze   # consome pra Parquet
   ```

2. **Faze mudanças e push novos commits**
   ```bash
   git add . && git commit -m "Seu commit message"
   git push
   ```

3. **Compartilhar a URL** no seu portfólio/LinkedIn:
   ```
   https://github.com/SEU_USUARIO/gridsense-simulator
   ```

---

**Dúvidas?** Veja os arquivos:
- `CONTRIBUTING.md` — como contribuir/desenvolver
- `GITHUB_SETUP.md` — instruções em inglês (mais detalhado)
- `README.md` — visão geral do projeto
- `simulator/README.md` — detalhes da Phase 1

Sucesso! 🚀

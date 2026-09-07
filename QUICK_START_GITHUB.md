# GitHub Setup — Quick Reference

## TL;DR

```bash
# 1. Criar repositório vazio em https://github.com/new
# 2. Terminal: vá até a pasta gridsense-simulator
# 3. Rode:
bash scripts/init_github.sh SEU_GITHUB_USER gridsense-simulator
# Pronto!
```

## Se quiser fazer manualmente:

```bash
git init
git add .
git commit -m "Initial commit: GridSense Simulator"
git remote add origin https://github.com/SEU_USER/gridsense-simulator.git
git branch -M main
git push -u origin main
```

## Verificar:

```
https://github.com/SEU_USER/gridsense-simulator
```

---

**Versão completa em português:** veja `GITHUB_SETUP_PT.md`
**Versão em inglês (detalhado):** veja `GITHUB_SETUP.md`

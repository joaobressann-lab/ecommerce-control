# Controle Geral do E-commerce · Grupo Carlota Costa

Dashboard integrado multi-marca (Nuvemshop + GA4 + Google Ads + Meta Ads) com camada de insights. Ver `CLAUDE.md` na raiz para a especificacao completa (arquitetura, regras de negocio, schemas, fases).

## Estrutura

```
pipeline/            codigo Python do pipeline de dados
  config.py          leitura de credenciais (env/Secrets) e definicao das lojas
  http.py            cliente HTTP Nuvemshop: auth, retry, rate limit, paginacao por Link
  brands.py          extracao de ref (regex validada) + atribuicao de marca por prefixo
  nuvemshop.py       conector: produtos, variantes, pedidos, mapa EAN -> ref
  run_nuvemshop.py   CLI de diagnostico do conector (censo + relatorio de excecoes)
data/                saida dos JSONs (commit automatico pela Action)
dashboard/           dashboard HTML standalone (Netlify)
.github/workflows/   cron diario 06h BRT + commit automatico
```

## Rodar local (dev)

Requer Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # e preencher as credenciais
python -m pipeline.run_nuvemshop --loja 1 --dias 30
```

Em producao as credenciais vivem em GitHub Secrets, nunca em arquivo. Nada hardcoded.

## Status F1

- [x] Estrutura do repo
- [x] Conector Nuvemshop (produtos, variantes, pedidos, mapa EAN -> ref, relatorio de excecoes)
- [x] Classificacao de canal auditada em pedidos reais: campo `storefront` (store/mobile = site, api = ANYMARKET)
- [x] Builder produtos.json + diario.json (score parcial ate GA4)
- [x] GitHub Action + commit automatico (cron 09h UTC / 06h BRT) validada em producao
- [x] Conector GA4 (4 relatorios da secao 4.2), agregado na ref pai via EAN, validado em producao
- [x] Duas lojas no pipeline (Loja 1 Carlota+Simone, Loja 2 Ritmi), run completo validado
- [ ] Dashboard Aba 2 + Aba 1
- [ ] Solicitar developer token Google Ads (em andamento com o Joao)

### Numeros da execucao real (Loja 1, 02/08/2026)
- 2816 produtos no censo (1662 publicados); regex de ref casou 2815/2816.
- Relatorio de excecoes bateu com o audit do CLAUDE.md: 12 EANs duplicados + 1 produto sem ref.
- Site (store+mobile) 30d: R$ 182.420,19 em 616 transacoes (Mobile 522, Loja virtual 94).
- GA4: 844+ produtos com funil/origens/score completo; 65 mil views/30d; canal por storefront.
- Categorias resolvidas como departamento (Blusas, Vestidos, Calcas...); AUDITAR PDP dispara (129).

### A corrigir no cadastro (relatorio em data/excecoes_nuvemshop.json)
- Produto "BLUSA ML COM AMARRACAO" sem ref no nome + 12 EANs duplicados.
- Categoria digitada como "BLUSA" (typo) em 1 produto.
```

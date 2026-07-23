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
- [ ] Auditar classificacao de canal do pedido com payload real (secao 9 do CLAUDE.md)
- [ ] Conector GA4 (4 relatorios da secao 4.2)
- [ ] Builder produtos.json + diario.json
- [ ] GitHub Action + commit automatico
- [ ] Dashboard Aba 2 + Aba 1
- [ ] Solicitar developer token Google Ads
```

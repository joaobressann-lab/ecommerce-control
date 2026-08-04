# Controle Geral do E-commerce · Grupo Carlota Costa

Dashboard integrado multi-marca: Nuvemshop + GA4 + Google Ads + Meta Ads, com camada de insights gerados por Claude. Evolução do Dashboard VM de Sale (padrão visual e de score já validado).

## 1. Visão geral da arquitetura

```
[Nuvemshop API x2]  [GA4 Data API]  [Google Ads API]  [Meta Marketing API]
        |                 |                |                  |
        +--------- pipeline Python (GitHub Actions, cron diário 06h BRT) ---------+
                                    |
                     normalização + cruzamento por REF
                                    |
              data/produtos.json · data/diario.json · data/insights.json
                                    |
                  commit automático no repo (joaobressann-lab)
                                    |
        dashboard HTML standalone (Netlify) lê de raw.githubusercontent
```

Princípios:
- Chave universal de cruzamento: **referência do produto (ref)**. Auditar item_id no GA4 e retailer_id nos catálogos antes de tudo (tarefa F1.1).
- Dashboard lê SEMPRE de raw.githubusercontent, nunca jsDelivr (cache de horas no @main).
- Tokens e credenciais só em GitHub Secrets. Nada hardcoded.
- Dashboard 100% HTML standalone, sem build step, CSS inline ou <style> no próprio arquivo.

## 2. Lojas, marcas e credenciais

| Loja Nuvemshop | Marcas | Credencial |
|---|---|---|
| Loja 1 | Carlota Costa + Simone Gomes | NUVEMSHOP_STORE1_ID / NUVEMSHOP_STORE1_TOKEN |
| Loja 2 | Ritmi Studio (+ mix) | NUVEMSHOP_STORE2_ID / NUVEMSHOP_STORE2_TOKEN |

Atribuição de marca pela taxonomia de prefixo (convenção persistente do grupo):
- REST / RESB / RIR → Ritmi Studio
- RIT → RIT
- RLC / RCC / RL → Sobras/PL
- CS* → Simone Gomes
- demais C* → Carlota Costa

Filtro de marca global no topo do dashboard, valendo para todas as abas.

## 3. Regras de negócio (invariantes, não negociar)

- Vendas do site: Canal in (Loja virtual, Mobile). Excluir ANYMARKET e Pedidos manuais.
- CSV/exports Nuvemshop quando usados: separador ";", encoding Latin-1, estrutura multi-linha por pedido exigindo ffill.
- Match de variante: ref + nome de cor TC normalizado (sem acento, sem espaço, sem "TC"), fallback pantone.
- Grade cheia: P/M/G completos em pelo menos 1 cor.
- Estoque crítico: ≤ 2 peças.
- Nunca usar travessão (—) em nenhum texto ou copy. Usar ponto, vírgula, ":" ou "·".
- Paleta: berry #7A1E3C · berry-deep #571028 · amber #C88A2B · gold #E7C171 · ivory #FAF6EF.
- Fontes: Fraunces (títulos) + Inter (texto) + JetBrains Mono (números).

## 4. Fontes e conectores

### 4.1 Nuvemshop (x2 lojas)
- Endpoints: /products (com variants: estoque por tamanho/cor, preço, promotional_price, published), /orders (itens, valores pagos, canal, cupom, data).
- Paginação: per_page=200, seguir header Link.
- Saída por produto: estoque por variante, publicado, preço cheio, preço promocional, vendas por dia, preço médio efetivamente pago (para desconto médio real).

### 4.2 GA4 Data API (runReport, service account)
Duas propriedades (uma por loja) ou uma com filtro de stream: confirmar na F1.
Relatórios necessários:
1. **Item x métricas**: dimensões [itemId], métricas [itemsViewed, itemsAddedToCart, itemsCheckedOut, itemsPurchased, itemRevenue]. Período: janela do filtro + últimos 30d para série.
2. **Item x origem**: dimensões [itemId, sessionSource, sessionMedium] (ou sessionDefaultChannelGroup como visão simplificada), métricas [itemsViewed, itemsAddedToCart, itemsPurchased, itemRevenue]. Guardar top 8 origens por produto, agregando o resto em "outros".
3. **Item x dia**: dimensões [itemId, date], métricas [itemsViewed, itemsPurchased]. Alimenta o sparkline.
4. **Site x dia x canal**: dimensões [date, sessionDefaultChannelGroup], métricas [sessions, totalUsers, transactions, purchaseRevenue]. Alimenta aba Funil/Tráfego e Visão Geral.
Nota conceitual: "sessões do produto" = itemsViewed (views de PDP). Sessão literal é dimensão de sessão e não cruza limpa com item.

### 4.2b De-para EAN → ref (OBRIGATÓRIO, auditado em 23/07/2026)
O item_id do GA4 é o EAN-13 da VARIANTE (cor+tamanho), campo sku da Nuvemshop. Auditoria no export da Loja 1: 1.628 produtos, 21.150 variantes, cada uma com EAN próprio.
- O pipeline constrói o mapa EAN → variante → ref pai a partir da API Nuvemshop (/products → variants[].sku) a cada execução. Nunca depender de export manual.
- Extração da ref a partir do nome do produto, regex validada (1.627/1.628): `[-–]\s*([A-Z]{1,6}\d{4,}(?:\.[A-Z0-9]+)?)\s*$` (cobre sufixos .2, .C, .1 etc.). Produtos sem match entram em relatório de exceções para correção no cadastro (caso conhecido: "BLUSA ML COM AMARRAÇÃO").
- Todos os dados GA4 (views, add_cart, compras, origens) são agregados na ref pai via esse mapa.
- Compras GA4 carregam o EAN da variante comprada; views NÃO são confiáveis por variante (view_item dispara com a variante padrão da PDP). Views sempre agregadas no pai.
- Vendas por cor e tamanho: fonte primária é a API de pedidos Nuvemshop (variante exata em cada item), não o GA4.
- 12 EANs duplicados detectados no cadastro (sempre intra-produto, ex.: CSI261655.2, CI261860.1, CAV250102). Não afetam agregação por ref; tornam ambígua atribuição GA4 por variante nesses casos. Pipeline gera lista desses EANs no relatório de exceções para correção no Genesis.

### 4.3 Meta Marketing API
- Graph API /insights nos níveis campaign, adset, ad: spend, impressions, clicks, ctr, cpm, purchase_roas, actions(purchase).
- Por produto: só se retailer_id do catálogo = ref. Auditar na F1; se não bater, breakdown por produto no Meta fica para depois e o custo Meta entra agregado por campanha.
- Token de sistema (System User) no Business Manager, sem expiração curta.

### 4.4 Google Ads API
- Developer token: **iniciar solicitação já na F1** (aprovação demora semanas).
- Enquanto não sai: GA4 já traz googleAdsCost/googleAdsClicks se as contas estiverem linkadas; usar como plano B para o blended.
- Quando aprovar: GAQL em campaign e shopping_performance_view (item_id = ref) para custo/ROAS por produto.

## 5. Schemas dos JSONs

### 5.1 data/produtos.json (uma entrada por ref, censo completo do catálogo: com venda, sem venda, não publicado)
```json
{
  "gerado_em": "2026-07-23T06:00:00-03:00",
  "periodo": {"inicio": "2026-06-23", "fim": "2026-07-23"},
  "produtos": [
    {
      "ref": "CSV1234",
      "nome": "Vestido Midi Alfaiataria",
      "marca": "Simone Gomes",
      "loja": 1,
      "categoria": "Vestidos",
      "foto": "https://...",
      "publicado": true,
      "preco_cheio": 389.90,
      "preco_promocional": 299.90,
      "preco_medio_pago": 287.40,
      "desconto_medio_pct": 26.3,
      "estoque_total": 18,
      "grade": {"cores": 3, "cheia": true, "por_tamanho": {"PP": 2, "P": 5, "M": 6, "G": 4, "GG": 1}},
      "variantes": [
        {"cor": "PRETO", "pantone": "19-4005", "estoque": {"PP": 1, "P": 2, "M": 3, "G": 2, "GG": 0}, "vendas_por_tamanho": {"PP": 0, "P": 1, "M": 2, "G": 1, "GG": 0}, "vendas_periodo": 4, "vendas_30d": 6, "eans": {"PP": "7890390210362", "P": "7890390210379"}}
      ],
      "funil": {"views": 812, "add_cart": 96, "checkout": 41, "compras": 23, "cv_view_cart": 11.8, "cv_cart_compra": 24.0, "cv_geral": 2.83},
      "vendas": {"periodo": 23, "d30": 31, "receita_periodo": 6610.20},
      "origens": [
        {"source": "facebook", "medium": "cpc", "views": 340, "add_cart": 41, "compras": 11, "receita": 3164.90},
        {"source": "(direct)", "medium": "(none)", "views": 210, "add_cart": 18, "compras": 4, "receita": 1149.60}
      ],
      "midia": {"google_custo": 84.20, "google_roas": 5.1, "meta_custo": 132.50, "meta_roas": 4.2},
      "serie_90d": [{"d": "2026-06-24", "views": 31, "compras": 1}],
      "mensal_24m": [{"m": "2026-06", "vendas": 28, "receita": 8120.40, "views": 940, "compras": 25}],
      "score": 87.4,
      "classe": "ESTRELA"
    }
  ]
}
```

### 5.2 data/diario.json (série por dia x loja x marca x canal de aquisição)
```json
{"linhas": [{"d": "2026-07-22", "loja": 1, "marca": "Carlota Costa", "canal_ga4": "Paid Social", "sessoes": 1240, "transacoes": 18, "receita": 5320.10, "custo_meta": 210.00, "custo_google": 95.00}]}
```

### 5.3 data/insights.json (gerado pela camada Claude no build)
```json
{"gerado_em": "...", "insights": [{"tipo": "alerta", "severidade": "alta", "titulo": "ESTRELA com estoque crítico", "texto": "CSV1234 vendeu 6 un em 30d e está com 2 peças na grade M.", "refs": ["CSV1234"], "acao": "Reposição ou reduzir mídia no produto."}]}
```

## 6. Score e classes (evolução do score sale)

Score completo (com GA4 disponível):
- Vendas 30% (60% peso últimos 30d)
- Conversão do produto 20%
- Views PDP 10%
- Estoque 15%
- Grade P·M·G 25%
- Penalidade 50% se não publicado. Penalidade forte para estoque zerado com demanda.

Classes: ESTRELA (≥2 un/30d + grade cheia) · VENDEDOR · BOM · AUDITAR PDP (tem tráfego, não converte) · PARADO (≥6 pçs sem venda) · BAIXO GIRO · ESTOQUE CRÍTICO (≤2 pçs) · ESGOTADO c/ demanda · NÃO PUBLICADO.
"AUDITAR PDP" agora tem gatilho objetivo: views acima da mediana da categoria e cv_geral abaixo de 40% da mediana.

## 6.5 Sistema de filtros globais (cross-filtering)

Barra de filtros fixa no topo, acima das abas. Todo filtro aplicado se propaga para TODAS as abas simultaneamente. Estado dos filtros persistido na URL (query params) para compartilhar visões.

Filtros disponíveis:
- **Data**: presets 7d · 30d · 90d + seletor personalizado: dia específico, intervalo livre, mês fechado e ano. Comparativo automático com período anterior equivalente. Granularidade dupla no cliente: ranges dentro dos últimos 90d usam a série diária (serie_90d); mês/ano fechado ou ranges mais antigos usam os agregados mensais (mensal_24m), com aviso discreto quando a granularidade for mensal.
- **Marca**: Carlota Costa · Simone Gomes · Ritmi Studio · RIT · Sobras/PL (multi-select).
- **Loja**: Loja 1 · Loja 2.
- **Canal de venda**: Loja virtual · Mobile · ANYMARKET (marketplaces) · Pedidos manuais (multi-select). Default: Loja virtual + Mobile. IMPORTANTE: na aba Produtos, o Modo VM (ver Aba 2) sobrepõe este filtro e força só site. Fora do modo VM, o filtro muda o que é exibido, com badge "score: só site" quando ANYMARKET estiver incluído.
- **Origem de mídia (GA4)**: Meta (facebook/instagram cpc) · Google cpc · Direct · Orgânico · Edrone/email · Outros. Filtra os funis, as views e as compras atribuídas nas abas 1, 2 e 3.
- **Coleção**: detecção automática por padrão prefixo+ano (convenção existente das análises de coleção). Multi-select.
- **Categoria**: via CAT_MAP.
- **Faixa de preço**: derivada do preço promocional vigente (até 99 · 100-199 · 200-299 · 300-399 · 400+), ajustável.
- **Classe** (só aba Produtos): ESTRELA, VENDEDOR, etc.
- **Só publicados / só grade cheia / faixa de desconto / origem dominante** (aba Produtos).

Implicação de granularidade nos dados:
- diario.json: linha por dia × loja × marca × canal de venda × canal GA4. É a base que permite qualquer recorte de data/canal nas abas 1 e 3. Mantém granularidade diária pelos últimos 24 meses direto (nível site/canal, arquivo pequeno).
- produtos.json: granularidade dupla por produto. (a) serie_90d diária (views, add_cart, compras, receita por canal de venda site/marketplace) para o filtro de data recalcular funil e vendas no cliente em ranges recentes; (b) mensal_24m: agregados mensais dos últimos 24 meses (vendas, receita, views, compras) para mês/ano fechado e ranges mais antigos que 90d. O breakdown de origens vem pré-agregado nos presets (7/30/90d) para não explodir o tamanho do arquivo; range personalizado usa o preset mais próximo com aviso discreto.
- Coleção e faixa de preço são derivadas no cliente (prefixo+ano e preço), não precisam vir do pipeline.
- Orçamento de tamanho: produtos.json alvo < 8 MB (gzip do GitHub raw resolve). Se passar, dividir por loja (produtos-loja1.json, produtos-loja2.json).

## 7. Abas do dashboard

### Aba 1 · Visão geral
KPIs do período vs período anterior: receita, pedidos, ticket médio, sessões, conversão do site, investimento total (Meta + Google), ROAS blended, CPA. Gráfico diário receita x investimento.
Rankings do período (todos respondendo aos filtros globais):
- Categorias mais vendidas (receita e unidades, com variação vs período anterior)
- Coleções mais vendidas (prefixo+ano)
- Faixas de preço mais vendidas
- Canais que mais vendem (Loja virtual, Mobile, ANYMARKET, manual) e origens de mídia que mais vendem (GA4)
- Quebra por loja e por marca
Cada item de ranking é clicável: aplica o valor como filtro global e navega junto.

### Aba 2 · Produtos (o coração)
Censo completo: TODOS os produtos, com venda, sem venda, não publicados.

**Modo VM (toggle destacado no topo da aba).** O score É o VM: sequência operacional aplicada nos menus da Nuvemshop. Com o modo VM ativado:
1. Vendas SEMPRE e somente site (Loja virtual + Mobile), ignorando o filtro global de canal. Marketplace e manual fora do cálculo, sem exceção.
2. Só refs publicadas entram na sequência.
3. A coluna # VM renumera 1..N DENTRO do recorte filtrado. Filtros de marca + categoria + coleção são combináveis e geram um VM personalizado por menu (ex.: Vestidos × Alto Verão 26 × Simone Gomes = sequência pronta para aplicar no menu de vestidos). Top 10 do recorte destacado.
4. Botão "Exportar sequência": lista ordenada (posição, ref, nome, cor principal, score) em CSV e em texto copiável, para aplicar a ordenação na Nuvemshop.
Com o modo VM desligado, a aba opera como análise: filtros globais de canal valem (pode incluir ANYMARKET), score exibido como métrica informativa com badge "score: só site".

Linha fechada (tabela): # posição fixa (ordenada por score desc, top 10 destacado) · foto · ref + nome · marca · badge publicado · grade PP·P·M·G·GG em chips com quantidades (laranja ≤2, ✓ GRADE CHEIA) · nº cores · estoque · preço cheio · preço promo · desconto médio real % · views · add cart · compras · conversão · ROAS · classe · score com barra · sparkline 30d (views + compras).

Linha expandida (clique na linha): 4 painéis.
1. **Funil do produto**: views → add cart → checkout → compra, com taxa de cada etapa e comparação com a mediana da categoria.
2. **Origens**: tabela source/medium com views, add cart, compras, receita e % de participação. É o painel de decisão: venda concentrada em Meta → escalar no Ads; tráfego direct alto sem conversão → ajustar VM/PDP.
3. **Variantes: matriz cor × tamanho**. Linhas = cores, colunas = PP·P·M·G·GG. Cada célula: estoque + vendas do período (fonte: pedidos Nuvemshop). Célula com venda e estoque ≤2 destacada em laranja (repor), célula com estoque ≥4 e zero venda esmaecida (parada). Totais por cor e por tamanho nas bordas, classe por cor (herda padrão do VM de Sale aba 2).
4. **Série 30d ampliada**: views e compras sobrepostos, marcações de mudança de preço se detectada.

Filtros: busca, marca, loja, categoria, classe, só publicados, só grade cheia, faixa de desconto, origem dominante.

### Aba 3 · Funil e tráfego
Canais de aquisição (sessionDefaultChannelGroup): sessões, conversão, receita por canal e por dia. Funil agregado do site. Comparativo entre lojas.

### Aba 4 · Mídia paga
Google e Meta lado a lado: campanha, custo, cliques, CPC, conversões, ROAS, evolução diária. Alerta visual para campanha com custo subindo e ROAS caindo (7d vs 7d anteriores).

### Aba 5 · Insights Claude
Renderiza insights.json: alertas (severidade alta primeiro), mudanças de classe desde ontem, oportunidades (produto com conversão alta e pouco tráfego pago), resumo executivo do dia. Fase 2: botão de chat com contexto do filtro ativo (aguarda resolução do acesso à API).

## 8. Fases de execução

**F1 (fundação + o salto maior)**
1. Auditar item_id no GA4 das duas lojas (bater com ref) e retailer_id nos catálogos Meta/Google.
2. Criar repo `ecommerce-control` (código pipeline + data/ + dashboard/).
3. Conector Nuvemshop x2 (produtos, variantes, pedidos, preço médio pago).
4. Conector GA4 (4 relatórios da seção 4.2), service account.
5. Builder do produtos.json + diario.json.
6. GitHub Action com cron diário + commit automático.
7. Dashboard: Aba 2 completa (linha + expansão) + Aba 1 básica.
8. Iniciar solicitação do developer token Google Ads (paralelo, demora).

**F2**: Meta Marketing API (system user token) + Aba 4 lado Meta + custo no blended da Aba 1.

**F3**: Google Ads API aprovada → custo/ROAS por produto via shopping_performance_view + Aba 4 completa + Aba 3.

**F4**: camada de insights (geração no build) + Aba 5. Fase posterior: chat em runtime.

## 9. Pendências a confirmar com João antes/durante F1

- [ ] Nome exato e URL das duas lojas Nuvemshop + gerar credenciais de API (app privado em cada loja).
- [ ] GA4: uma propriedade por loja? IDs das propriedades. Acesso admin para criar service account.
- [ ] Confirmar se item_id enviado ao GA4 é a ref ou o ID interno Nuvemshop (define se precisa tabela de-para).
- [ ] Business Manager Meta: acesso para criar System User.
- [x] item_id GA4 = EAN da variante (auditado 23/07). De-para automático especificado na seção 4.2b.
- [ ] Corrigir no cadastro: produto "BLUSA ML COM AMARRAÇÃO" sem ref no nome + 12 EANs duplicados (lista sai no relatório de exceções do pipeline).
- [ ] Google Ads: ID da MCC/conta para solicitar developer token.
- [ ] Composição exata da Loja 2 ("mistura"): quais marcas/prefixos entram lá.

## Estado atual do dashboard (F1.7 em andamento, pausa de 04/08/2026)

### O que ja foi feito
- Pipeline completo e validado em producao para as duas lojas (F1.1 a F1.6 + F1.8 protocolado).
  produtos.json, diario.json e excecoes_nuvemshop.json sao gerados pela GitHub Action
  (cron 06h BRT) e commitados no repo. O dashboard le via raw.githubusercontent.
- dashboard/index.html criado: HTML standalone, sem build step, CSS e JS inline,
  fontes Fraunces + Inter + JetBrains Mono via Google Fonts. Le produtos.json e
  diario.json reais.
- Barra de filtros global no topo (busca, marca multi-select, loja, categoria, classe,
  so publicados, so grade cheia) + toggle Modo VM. Estado dos filtros persistido na URL.
- Aba Produtos (o coracao): tabela com # posicao, foto, ref+nome, marca (com cor),
  badge publicado, grade PP.P.M.G.GG em chips (laranja para estoque <=2, check para
  grade cheia), cores, estoque, preco, desconto medio, views, cart, compras, conversao,
  ROAS (placeholder ate F2), classe (badge colorido), score com barra, sparkline 30d
  (views em linha azul + compras em barras berry). Top 10 destacado com faixa dourada.
  Linha expandida com 4 blocos: funil do produto, origens GA4, matriz cor x tamanho
  (celula repor em laranja quando vendeu e estoque <=2; parada esmaecida), serie 30d.
  Botao Exportar sequencia (CSV) para aplicar a ordenacao VM na Nuvemshop.
- Aba Visao geral (basica): KPIs do recorte (receita, pedidos, ticket, sessoes GA4,
  conversao; investimento/ROAS/CPA como placeholder ate F2/F3), rankings clicaveis
  (categorias, canais de venda, receita por marca) e grafico de receita diaria do site.
- Tema claro/escuro com toggle e persistencia em localStorage.

### Decisoes de design tomadas
- Paleta do chrome: berry/amber/gold/ivory do CLAUDE.md (secao 3).
- Series de dados: paleta CATEGORICA VALIDADA pela skill dataviz (rodando o
  validate_palette.js), nao a paleta de marca ad-hoc (que reprovou nos gates de
  CVD/contraste/banda de luminosidade). Mapeamento fixo por marca (cor segue a
  entidade, nao o rank): Carlota=slot1 azul, Simone=slot2 laranja, Ritmi=slot3 aqua,
  RIT=slot4 amarelo, Sobras/PL=slot5 magenta. Versoes light e dark de cada slot.
- Score bar: hue unica (gradiente amber->berry), nao categorica.
- Modo VM: quando ligado, forca so site + so publicados e a coluna # renumera no
  recorte filtrado; badge explica. Fora do VM, badge "score: so site".
- Tabela pagina em blocos (150 inicial, botao carregar mais) por causa dos 3472 produtos.

### Proximo passo
1. Renderizar e revisar visualmente o dashboard (nao consegui abrir em navegador
   nesta maquina; validar layout, colisoes de rotulo e overflow, conforme passo 7 da
   skill dataviz). Publicar no Netlify apontando para dashboard/index.html.
2. Completar a Aba 1 (comparativo com periodo anterior, mais rankings: colecoes por
   prefixo+ano e faixas de preco derivadas no cliente) e adicionar as abas 3 (Funil e
   trafego, usando diario.trafego do GA4) e 4/5 nas fases seguintes.
3. Tooltips de hover nos graficos (crosshair na serie, por-marca nos rankings),
   conforme a skill dataviz (atualmente so ha <title> nos bars).
4. Filtros ainda nao implementados no cliente: data (presets 7/30/90d + seletor
   personalizado: dia especifico, intervalo livre, mes e ano), canal de venda,
   origem de midia, colecao, faixa de preco. A serie por produto hoje e serie_30d;
   para o filtro de data recalcular no cliente sera preciso serie_90d + agregados
   mensais mensal_24m de 24 meses (ver secao 6.5).

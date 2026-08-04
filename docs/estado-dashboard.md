# Estado atual do dashboard

> Nota: este arquivo existe porque o CLAUDE.md nao esta versionado no repositorio
> (nao esta em disco no working dir, so o README). O conteudo abaixo esta pronto
> para ser colado como uma secao nova no CLAUDE.md canonico do Joao. Sem travessao,
> conforme a regra do projeto.

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
4. Filtros ainda nao implementados no cliente: data (presets 7/30/90d), canal de venda,
   origem de midia, colecao, faixa de preco. A serie por produto hoje e serie_30d;
   para o filtro de data recalcular no cliente sera preciso serie_90d (ver secao 6.5).

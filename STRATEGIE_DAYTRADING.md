# Bot n°2 : day trading sur actions américaines (27 novembre 2026)

> Les versions crypto du bot n°2 (cassure de volatilité sur BTC, ETH, SOL…, v1 et v2) sont abandonnées : le bot trade désormais des actions US, moins volatiles que les cryptos. Leur code reste dans l'historique git.

## 1. En une phrase

Chaque séance, le bot **vend à découvert à l'ouverture les 15 actions liquides qui ont le plus bondi avant l'ouverture (+3 % ou plus), sauf annonce de rachat, et les rachète à la clôture**, avec un stop de sécurité à +15 % par titre. Aucune position la nuit.

## 2. Pourquoi cette idée

- **Les gaps haussiers « d'attention » refluent souvent dans la journée.** Les actions qui ouvrent en forte hausse attirent les acheteurs particuliers au moment de l'ouverture, puis les prix reviennent en partie en arrière pendant la séance. C'est documenté sur les actions US : rendements de la nuit élevés suivis de baisses en séance pour les titres très suivis (Berkman, Koch, Tuttle & Zhang, 2012), et « tir à la corde » entre rendements de nuit et de séance (Lou, Polk & Skouras, 2019). *Références citées de mémoire, non revérifiées en ligne pour ce document.*
- **Pourquoi ça peut durer :** vendre à découvert est plus contraignant qu'acheter (emprunt des titres, risque d'envolée), donc les excès à la hausse sont moins vite corrigés que les excès à la baisse. Nos données le confirment : le reflux des gaps haussiers est net, le rebond des gaps baissiers n'existe pas (§ 4).
- **Rappel honnête :** la grande majorité des day traders particuliers perdent de l'argent (Taïwan : moins de 1 % gagnent régulièrement, Barber, Lee, Liu & Odean ; Brésil : environ 3 % gagnent, Chague, De-Losso & Giovannetti). Un avantage de backtest n'est pas une garantie.

## 3. Le marché au moment du lancement (fin novembre 2026)

- L'indice S&P 500 était encore en hausse d'environ 8 % sur 2026 mi-novembre, mais la fin du mois est agitée : le Nasdaq a perdu 2,5 % et l'indice des semi-conducteurs 5,3 % en une séance après la démission de Jensen Huang de la tête de Nvidia, et le 17 novembre 17 actions du S&P 500 sur 20 ont baissé.
- Agenda : réunion de la Fed les 9 et 10 décembre, emploi le 16 décembre, inflation le 18 décembre. Bourse fermée le 26 novembre (Thanksgiving), demi-séance le 27.
- Conséquence pour le bot : beaucoup de titres très suivis (IA, semi-conducteurs) et donc beaucoup de gaps. Le bot ne prédit pas le marché : il applique la même règle chaque jour.
- Réglementation : la règle américaine du « pattern day trader » (25 000 $ minimum pour faire plus de 3 allers-retours en 5 jours) a été supprimée — approbation de la SEC en avril 2026, entrée en vigueur le 4 juin 2026.

Sources : Reuters via The Standard (HK), AP (17 nov. 2026), Barchart/Nasdaq.com, Federal Register (17 avril 2026), FINRA (avis 26-10 et archive du 22 avril 2026), E*TRADE (5 juin 2026).

## 4. Six familles de stratégies testées

Données Yahoo Finance : 5 362 actions ordinaires US (listes Nasdaq Trader), 10 ans en journalier ; 2 722 actions liquides en bougies d'une heure sur 730 séances (29/12/2023 → 25/11/2026) ; 1 960 actions en bougies de 5 minutes sur 60 séances (2/09 → 25/11/2026), pré-séance comprise. Frais : 0,05 % par ordre (0,03 % de glissement + 0,02 % de commission et taxes), soit 0,10 % aller-retour, sauf mention.

| Stratégie | Échantillon | Résultat | Verdict |
|---|---|---|---|
| Cassure de la 1re bougie de 5 min sur les « stocks in play » (Zarattini, Barbon & Aziz) : top 20 par volume relatif, stop à 10 % de l'ATR | 45 séances, 5 min | −43 % (−22 % même en hypothèses favorables, sans glissement) | rejetée |
| Même chose avec la fourchette des 30 premières minutes | 45 séances | +5 % sans glissement, −4 % avec | rejetée |
| Momentum intraday « zone de bruit » sur SPY / QQQ (Zarattini, Aziz & Barbon) | 2,5 ans, contrôles horaires | SPY −14 %/an, QQQ +6 %/an | rejetée (pas robuste chez nous) |
| Acheter les gaps baissiers (rebond) | 2,9 ans, 1 h | −39 %/an | rejetée |
| Vendre les gaps baissiers (continuation) | 2,9 ans, 1 h | −23 %/an | rejetée |
| **Vendre les gaps haussiers d'attention** | 10 ans, 2,9 ans, 60 séances | **positive sur les trois échantillons** | **retenue** |

Constat : sur les données de 2026, la cassure d'ouverture très médiatisée ne marche plus avec des frais réalistes ; le seul avantage qui résiste vient du reflux des gaps haussiers.

## 5. La stratégie retenue

| Étape | Règle |
|---|---|
| Univers | actions ordinaires US (pas d'ETF), cours > 5 $, plus de 50 M$ échangés par jour en moyenne sur les 20 séances précédentes |
| 9:25 New York | gap de pré-séance = dernier cours de pré-séance à 9:25 ÷ clôture de la veille − 1 ; candidats entre +3 % et +60 % |
| Scan d'actualité | titres Yahoo Finance depuis la veille : une annonce de rachat, fusion, OPA ou offre en numéraire écarte le titre (son cours se colle au prix de l'offre et peut bondir sur une surenchère) ; les autres thèmes (résultats, analystes, FDA, émission d'actions…) sont affichés |
| Sélection | les 15 plus gros gaps |
| 9:30 | vente à découvert au prix d'ouverture officiel (ordre « au marché à l'ouverture »), 1/15 du capital par titre, soit 1× le capital au total |
| Protection | stop de rachat à +15 % au-dessus du prix de vente |
| 16:00 | rachat de tout au prix de clôture (ordre « au marché à la clôture ») |

Pourquoi ces réglages :
- **15 titres plutôt que 10 :** même rendement à risque égal sur 2,9 ans, mais pire repli −23 % au lieu de −31 %, et la seule version encore positive sur les 40 dernières séances avec un stop.
- **Stop à +15 % :** sans stop, le rendement est un peu meilleur, mais une action a pris +239 % dans la journée dans notre échantillon : sans stop, une seule position pourrait coûter 16 % du capital. Les stops plus serrés (1 à 3 ATR, 10 %) coupent trop de positions qui refluent ensuite.
- **Décisions supplémentaires en cours de journée (testé le 27/11) :** vendre ou acheter, à 10:30, 11:30, 12:30 ou 13:30, les titres qui montent de plus de 5 % ou 8 % dans la séance. Sur 2,9 ans, les ventes perdent de −8 à −25 points de base par trade et les achats ne gagnent presque rien ; sur les 40 dernières séances, c'est l'inverse (les ventes gagnent, les achats perdent lourdement). Un effet qui change de signe selon la période n'est pas fiable : **une seule décision par jour**, mais un suivi toutes les 10 minutes.
- **Écartés après test :** limite de perte journalière du portefeuille (−2 % à −5 %), ciblage de volatilité, taille selon la volatilité de chaque titre, couverture par achat de SPY, filtre « gap < 2 ATR » (aide sur 10 ans, nuit sur 2,9 ans et 60 séances), confirmation après 5 min, sortie avant la clôture. Aucun n'améliore le résultat de façon cohérente.

## 6. Les tests

### Un biais trouvé et corrigé
Choisir les titres sur le **prix d'ouverture officiel** (ce que fait un backtest journalier) gonfle les résultats : un titre qui ouvre plus haut que son cours de pré-séance reflue davantage, mais on ne peut pas le savoir avant d'envoyer l'ordre. Sur les 40 dernières séances, le gain moyen par trade passe de 0,75 % à 0,40 % quand on sélectionne sur la pré-séance ; sur 2,9 ans, de +110 %/an à +58 %/an (version à 10 titres sans stop). **Tous les chiffres « réalistes » ci-dessous sélectionnent sur la pré-séance.**

### Résultats de la version finale (frais 0,10 % aller-retour, capital 1×)

| Test | Période | Rendement | Sharpe | Pire repli | Trades |
|---|---|---|---|---|---|
| Bougies 1 h, pré-séance de 9:00 (le plus fiable) | 29/12/2023 → 25/11/2026 | **+40 %/an** (2024 +13 %, 2025 +66 %, 2026 +38 %) | 1,21 | −23 % | 9 978 |
| Bougies 5 min, pré-séance de 9:25 | 1/10 → 25/11/2026 (40 séances) | +4,8 % | 0,93 | −19 % | 516 |
| **Rejeu avec le code du bot** (séance par séance, données du jour seulement) | mêmes 40 séances | **+2,2 %** | 0,54 | −19 % | 508 |
| Journalier 10 ans, sélection sur l'ouverture (optimiste) | 2017 → 2026 | +53 %/an, 9 années positives sur 10 (2019 −19 %) | 1,52 | −30 % | — |

- **Robustesse :** sur 10 ans, 324 combinaisons de réglages (seuil de gap, taille maximale du gap, nombre de titres, stop, liquidité) donnent toutes un Sharpe supérieur à 1 (médiane 1,57).
- **Frais :** à 0,20 % aller-retour, +10 %/an ; à 0,30 %, −13 %/an. **La stratégie n'existe qu'avec des frais bas.**
- **Simulation sur 6 mois** (tirages de blocs de 10 séances du test 1 h) : médiane +19 %, 5 % des cas pires que −18 %, 5 % meilleurs que +72 %, **22 % de chances de perdre**, repli médian −16 %.
- **Concentration :** dans la version à 10 titres, le 1 % des meilleurs trades fait plus que la totalité du gain. Les résultats dépendent de quelques grosses chutes.
- **Test « dans le futur » :** le bot tourne en paper trading depuis le 27 novembre 2026 ; c'est le seul test sans aucun regard en arrière possible.

## 7. Risques et limites

1. **Envolées brutales.** Le bot est uniquement vendeur. Un titre peut être suspendu puis rouvrir bien au-delà du stop : la perte peut dépasser 15 % sur une position.
2. **Emprunt des titres.** Pour vendre à découvert il faut que le courtier prête les actions ; les titres les plus recherchés peuvent être indisponibles ou chers à emprunter. Le test suppose qu'on peut toujours vendre.
3. **Exécution à l'ouverture.** Les ordres doivent partir avant 9:28 (enchère d'ouverture). Un ordre au marché à 9:35 donnerait un résultat plus faible (dans un test sur 40 séances : 0,24 % par trade au lieu de 0,34 %).
4. **Biais de survie.** Les listes d'actions sont celles d'aujourd'hui : les sociétés disparues de la cote manquent dans les tests.
5. **Données Yahoo** non officielles ; les cours d'ouverture ont été vérifiés contre les bougies de 5 minutes (écart médian nul).
6. **Actualité :** le filtre « rachat » est une règle de prudence de trader, non testée (l'historique des titres n'est pas disponible gratuitement) ; il ne peut qu'écarter des ventes. Les dépôts 8-K de la SEC ne sont pas utilisés par le bot.
7. **Régime récent plus faible :** +2,2 % sur les 40 dernières séances contre +40 %/an en moyenne sur 2,9 ans.

## 8. Passer un jour en réel (non fait)

- Courtier avec vente à découvert d'actions US, ordres à l'ouverture et à la clôture, et frais très bas (par exemple une offre facturée à l'action). En dessous d'environ 25 000 $, les commissions minimales par ordre pèsent trop sur des positions de 1 700 $.
- Commencer avec une fraction du capital et comparer chaque jour aux résultats du paper trading.
- Fiscalité française : gains imposés au prélèvement forfaitaire unique (31,4 % en 2026), à vérifier avec un conseiller.

## 9. Fonctionnement du bot

- `stocks/live.py` : à chaque passage, il télécharge les bougies 5 min (pré-séance comprise) et **rejoue la séance** : scan figé sur les cours de 9:25, vente à l'ouverture, stops bougie par bougie, rachat à la clôture. Le résultat ne dépend donc pas de l'heure à laquelle GitHub lance le script, et aucune séance commencée avant le démarrage du bot n'est jouée.
- `stocks/news.py` : titres Yahoo Finance de chaque candidat, classés par thème.
- `stocks/universe.json` : actions à plus de 30 M$ échangés par jour, rafraîchie tous les 3 jours ; le seuil de 50 M$ est vérifié chaque matin avec les données de la veille.
- `.github/workflows/stocks.yml` : passages toutes les 10 minutes de 8:00 (New York) à la clôture, puis un passage final ; avant l'ouverture, une **liste provisoire** (mêmes règles, cours de pré-séance du moment, actualité) est affichée et rafraîchie toutes les 20 minutes, la décision restant figée à 9:25 ; l'état est dans `state/stocks/state.json`, la page lit `docs/stocks.json`.
- Recherche : `stocks/research.py`, `scripts/stocks_research.py` (tableaux et courbe de la page), `scripts/stocks_replay.py` (rejeu avec le code du bot) ; tests automatiques : `tests/test_stocks.py`.
- Page en direct : https://samuelnajman-prog.github.io/botcrypto/stocks.html

## Sources

- [Barber, Lee, Liu & Odean — The cross-section of speculator skill: evidence from day trading](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf)
- [Chague, De-Losso & Giovannetti — Day trading for a living?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101)
- [Zarattini, Barbon & Aziz — A profitable day trading strategy for the U.S. equity market](https://ideas.repec.org/p/chf/rpseri/rp2498.html)
- [Federal Register, 17 avril 2026 — approbation de la réforme de la règle 4210 de la FINRA](https://www.federalregister.gov/documents/2026/04/17/2026-07485/self-regulatory-organizations-financial-industry-regulatory-authority-inc-notice-of-filing-of) · [FINRA, avis 26-10](https://www.finra.org/rules-guidance/notices/26-10) · [E*TRADE, 5 juin 2026](https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change)
- [Reuters via The Standard — Wall Street après la démission de Jensen Huang](https://www.thestandard.com.hk/finance/article/348698/Wall-Street-ends-sharply-lower-on-AI-jitters-ahead-of-Nvidia-results-Anthropic-IPO) · [AP, 17 novembre 2026](https://www.ajc.com/news/2026/11/another-wipeout-hits-wall-street-as-nvidia-and-other-jittery-ai-stocks-swing-lower/) · [Nasdaq.com — agenda Fed, emploi, inflation](https://www.nasdaq.com/articles/stocks-fall-back-nvidia-drags-chip-makers-lower)

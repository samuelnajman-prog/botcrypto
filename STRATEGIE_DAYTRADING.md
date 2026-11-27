# Bot n°2 : day trading crypto (v2 « agressive », 27 novembre 2026)

## 1. En une phrase

Chaque jour, le bot place un ordre d'achat « en cas de cassure » sur 6 grandes cryptos en tendance. Il achète si le prix monte nettement au-dessus de l'ouverture, et revend tout à la clôture de la journée. Aucune position n'est gardée d'un jour sur l'autre.

## 2. Ce que disent les études avant de commencer

- **La grande majorité des day traders particuliers perdent de l'argent.**
  - À Taïwan, sur 15 ans, l'ensemble des day traders perd de l'argent chaque année, et moins de 1 % gagnent de façon régulière (Barber, Lee, Liu & Odean).
  - Au Brésil, parmi ceux qui ont persisté, environ 3 % ont gagné de l'argent et moins de 1 % plus que le salaire minimum (Chague, De-Losso & Giovannetti).
- **Les stratégies publiées les plus solides sont sur actions américaines.** Exemple : l'« opening range breakout » sur les actions qui bougent anormalement, avec un Sharpe de 2,8 annoncé sur 2016-2023 (Zarattini, Barbon & Aziz). Mais des réplications sur indices trouvent un résultat nul une fois les frais déduits, et des praticiens signalent un fort glissement de prix à l'ouverture.
- **En crypto, un momentum intraday existe** (Shen, Urquhart & Wang, 2022), mais il est petit face aux frais.

## 3. Sept stratégies testées sur 6 cryptos

Bougies 5 minutes Binance de janvier 2020 à octobre 2026. Réglage jugé sur 2020-2022, verdict sur 2023-2026. Script : `scripts/daytrade_research.py`.

| Stratégie | Sharpe avant frais (2023-26) | Sharpe après frais 0,10 % (2023-26) | Trades/an |
|---|---|---|---|
| Opening range breakout, séance US, 15 min | +1,20 | −1,56 | 1 398 |
| ORB 5 min + filtre « en mouvement » (volume relatif) | +0,28 | −1,24 | 262 |
| ORB 30 min, achat seul | +0,95 | −0,30 | 217 |
| Zone de bruit (Zarattini, Aziz & Barbon), journée UTC | +0,51 | −8,14 | 6 501 |
| Zone de bruit, séance US | +1,15 | −3,46 | 2 055 |
| Momentum de la dernière heure | 0,00 | −14,12 | 2 184 |
| **Cassure de volatilité + filtre de tendance** | **+1,87** | **+0,86** | **269** |

**Constat principal : presque toutes les méthodes de day trading ont un petit avantage avant frais, et les frais le font disparaître.** Seule la cassure de volatilité garde un avantage net, parce qu'elle trade peu (une fois tous les 5 à 6 jours par crypto) et capte des mouvements larges.

## 4. Règle de base (v1, remplacée par la v2 du §9)

1. **Univers :** BTC, ETH, SOL, BNB, XRP, DOGE (cryptos liquides seulement, car le day trading sur petites cryptos coûte trop cher).
2. **Niveau d'achat :** ouverture du jour (00:00 UTC) + 0,8 × l'amplitude (plus haut − plus bas) de la veille.
3. **Filtre de tendance :** l'ordre n'est posé que si l'ouverture est au-dessus de la moyenne des 10 dernières clôtures.
4. **Taille :** 1/6 du capital × min(1 ; 3 % / volatilité journalière sur 14 jours), sans levier.
5. **Sortie :** revente à la clôture du jour (00:00 UTC). Pas de stop intraday : les tests avec un stop au prix d'ouverture font nettement moins bien.
6. **Achat seulement :** ajouter des ventes à découvert dégrade les résultats sur les deux périodes.

Paramètres choisis au milieu du plateau des bons réglages (k = 0,7 à 0,8, moyenne 10 à 20 jours), pas au meilleur point.

## 5. Résultats et dépendance aux frais

Règle retenue, test 2023 → octobre 2026 :

| Frais par ordre | Par an | Sharpe | Pire baisse |
|---|---|---|---|
| **Contrats perpétuels (taker 0,05 % + financement)** | **+10,6 %** | **1,09** | **−8,6 %** |
| 0,10 % | +7,2 % | 0,77 | −9,7 % |
| OKX Europe au comptant, taker 0,20 % | −0,9 % | −0,05 | −14,5 % |
| Kraken Pro au comptant, taker 0,38 % | −14,0 % | −1,57 | −46,1 % |

Par année (1× par crypto, frais 0,10 %) : 2020 +41 %, 2021 +146 %, 2022 −13 %, 2023 +5 %, 2024 +23 %, 2025 −1 %, 2026 (à octobre) +7 %. Les gains viennent surtout des marchés très haussiers.

**La rentabilité dépend entièrement des frais.** Seuls des contrats perpétuels à frais bas (par exemple Kraken Derivatives pour l'EEE : 0,02 % maker / 0,05 % taker) la rendent intéressante. Au comptant chez OKX ou Kraken, elle ne gagne rien.

## 6. Ce que le bot fait en paper trading

- Toutes les heures (GitHub Actions), il lit les bougies 5 min et simule des ordres stop posés à 00:00 UTC : remplissage au niveau, ou à l'ouverture de la bougie si le prix saute au-dessus, plus 0,03 % de glissement et 0,06 % de frais par ordre.
- Il revend à la clôture de 00:00 UTC.
- Il n'y a pas d'achat rétroactif : si la cassure a eu lieu avant le démarrage du bot, la journée est ignorée.
- **Page en direct :** https://samuelnajman-prog.github.io/botcrypto/day.html (distance de chaque crypto à son niveau d'achat, positions, P&L du jour).

## 7. À surveiller

| Indicateur | Repère du test | Alerte |
|---|---|---|
| Trades | ~260 / an sur les 6 cryptos (~5 par semaine en marché haussier, 0 en marché baissier) | aucun trade en marché haussier = bug |
| Pire baisse | −9 % | au-delà de −15 % |
| Gain moyen par trade | ~+0,4 % brut | négatif sur 50 trades = les frais réels mangent tout |

Repères de la v2 : pire baisse attendue vers −27 %, alerte au-delà de −35 %.

## 8. Le skill « day-trader » (Jake Schincariol) : audit et améliorations

- **Audit :** le code ne contacte que des sources publiques (Yahoo, SEC, Nasdaq, House Clerk), ne passe aucun ordre tout seul et ne contient aucune instruction douteuse. Il est conçu pour les **actions américaines**, et son gestionnaire de risque bloque même la crypto. Commit audité : `3d0f4ef`.
- **Améliorations tirées des tests et de la littérature :**
  1. Filtre « actions en mouvement » (volume des 5 premières minutes vs moyenne 14 jours), cœur de l'étude Zarattini.
  2. Fourchette d'ouverture de 5 min plutôt que 15.
  3. Stop à 10 % de l'ATR et sortie à la clôture plutôt qu'un objectif fixe à 1R/2R, qui coupe les gros gagnants.
  4. Calcul systématique de l'espérance **après frais et glissement** dans le Time Machine.
  5. Avertissement : les résultats publiés ont souvent disparu une fois répliqués avec des frais réalistes.
- **Ces améliorations n'ont pas pu être testées sur actions :** Yahoo ne donne que 60 jours de bougies 5 min, ce qui est trop court pour valider quoi que ce soit.

## 9. Version agressive (v2, en service depuis le 27/11/2026)

Demande : viser plus de 10 % par an. Trois leviers ont été testés (216 combinaisons, reproduits dans `scripts/daytrade_research.py`) :
1. **Capital mutualisé :** les cryptos sans signal laissent leur part aux autres, au lieu de 1/6 fixe chacune.
2. **Levier :** jusqu'à 2, le plafond légal des CFD/perpétuels pour les particuliers dans l'UE.
3. **Univers de 12 cryptos** au lieu de 6.

Règle de sélection fixée avant de regarder la période de test : meilleur rendement 2020-2022 avec une pire baisse ≥ −25 % et un Sharpe ≥ 1,5.

**Règle retenue :**
- niveau = ouverture + 0,8 × amplitude de la veille ;
- filtre : ouverture au-dessus de la **moyenne 20 jours** ;
- chaque position = min(**34 %**, 6 % / volatilité 14 j) du capital, attribuée dans l'ordre des déclenchements ;
- exposition totale ≤ **1,5 × le capital** ;
- 6 cryptos, revente à la clôture.

| Test 2023-2026, frais de perpétuels | Par an | Sharpe | Pire baisse | Pire jour |
|---|---|---|---|---|
| v1 (1/6 par crypto, sans levier) | +12,3 % | 1,06 | −10,9 % | −3,3 % |
| **v2 agressive** | **+21,6 %** | **0,94** | **−27,5 %** | **−7,0 %** |
| v2 avec 1/4 par trade (variante modérée) | +17,7 % | 0,99 | −21,1 % | −5,2 % |
| v2 avec 1/2 par trade | +22,7 % | 0,82 | −37,1 % | −10,3 % |
| v2 avec levier 2 | +23,7 % | 0,99 | −27,6 % | −7,0 % |

Par année (v2) : 2020 +121 %, 2021 +379 %, 2022 −1 %, 2023 +7 %, 2024 +56 %, 2025 +14 %, 2026 (à octobre) +11 %. La pire baisse va du 20/01/2023 au 24/09/2023 (−27,5 %).

Constats :
- **L'agressivité ne rend pas la stratégie meilleure, elle la rend plus grosse.** Le Sharpe reste vers 0,9-1,0 : le rendement double, la pire baisse est multipliée par 2,5.
- Le levier au-delà de 1,5 n'apporte presque rien (rarement atteint).
- **Un stop « catastrophe » intraday dégrade tout** (test : +11 à +18 % par an au lieu de +22 %).
- **12 cryptos au lieu de 6 : pas mieux en test** (Sharpe médian 0,72 contre 0,86).
- **Les frais restent décisifs :** à 0,10 % par ordre la v2 tombe à +13 % par an, à 0,20 % (OKX au comptant) à −5 %.
- Avec un levier de 1,5 et des positions fermées chaque soir, il n'y a pas de risque de liquidation réaliste : il faudrait une chute d'environ 65 % du panier dans la journée.

## Sources

- [Barber, Lee, Liu & Odean — The cross-section of speculator skill: evidence from day trading](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf)
- [Chague, De-Losso & Giovannetti — Day trading for a living?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101)
- [Zarattini, Barbon & Aziz — A profitable day trading strategy for the U.S. equity market](https://ideas.repec.org/p/chf/rpseri/rp2498.html)
- [Réplication de l'ORB sur 5 indices : brut reproduit, net nul](https://www.mql5.com/en/blogs/post/776235)
- [Shen, Urquhart & Wang — Bitcoin intraday time-series momentum](https://research.birmingham.ac.uk/en/publications/bitcoin-intraday-time-series-momentum/)
- [Frais OKX Europe (grille du 25/09/2026)](https://cryptoactu.com/guides/frais-okx/) · [Kraken Pro vs OKX](https://cryptoactu.com/guides/okx-vs-kraken/) · [Frais des dérivés Kraken EEE](https://support.kraken.com/hc/fr/articles/fees-for-derivatives-trading-eea)

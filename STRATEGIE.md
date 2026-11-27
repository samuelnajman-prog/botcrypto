# Stratégie du bot crypto — v1 (23 novembre 2026)

## 1. Objectif et prémisse corrigée

Objectif posé : « gagner le plus d'argent possible sur ~6 mois ». Reformulé de façon tenable : **maximiser la croissance du capital sur 6 mois sans s'exposer à une perte qui détruit le projet**. Maximiser le rendement brut sur 6 mois, c'est maximiser l'exposition (levier, altcoins) : sur cet horizon, c'est surtout maximiser la probabilité d'un −50 %. Le bot vise donc le meilleur rapport rendement / repli, pas le rendement maximal.

Ce qu'un bot de ce type ne fait pas : prédire le prix. Il réagit aux tendances déjà installées, avec retard, et accepte de rater le début et la fin de chaque mouvement.

## 2. Contexte de marché au lancement

- BTC ≈ 61 300 $, ETH ≈ 1 720 $, SOL ≈ 72 $ (Binance, 23/11/2026). Capitalisation totale ≈ 2 100 Md$, dominance BTC ≈ 58 % (CoinGecko).
- BTC est à environ −51 % de son record d'octobre 2025 (~126 000 $), et a enchaîné six semaines de baisse en novembre 2026.
- Fear & Greed Index à 11 (« peur extrême », alternative.me).
- Flux : sorties record des ETF bitcoin spot américains en novembre 2026 (≈ 1 Md$ par semaine deux semaines de suite).
- Macro : marchés et plusieurs banques anticipent une **hausse** de taux de la Fed en décembre 2026, avec une inflation repartie à la hausse. Choc tech mi-novembre (démission du PDG de Nvidia, forte baisse du Nasdaq).
- Cycle : les deux derniers marchés baissiers ont touché leur creux ~12-13 mois après le sommet. Ce calendrier placerait un creux possible vers oct.-nov. 2026, mais rien ne garantit que le schéma se répète ; certains analystes voient le creux encore plus bas.
- Fiscalité France : plus-values crypto au PFU de **31,4 %** depuis 2026 (hausse de la CSG), seuil d'exonération 305 €.

**Conséquence pour le bot** : il démarre en régime RISK-OFF (BTC 61 271 < MM200 72 385), forecasts tous négatifs → **100 % stablecoin**. C'est voulu. Il n'achètera qu'une fois une tendance haussière confirmée, donc après le creux, jamais au creux.

## 3. Ce que dit la recherche (et pourquoi ces choix)

| Constat | Implication |
|---|---|
| Le *time-series momentum* (suivre la tendance propre de chaque actif) est bien documenté en crypto, même sous hypothèses réalistes ; le *cross-sectional momentum* (classer les cryptos entre elles) est beaucoup plus fragile (Han, Kang & Ryu ; Gbadebo 2026) | Signal principal = tendance de chaque actif ; le classement ne sert qu'à choisir parmi les actifs déjà haussiers |
| Le momentum crypto subit des krachs violents ; gérer l'exposition par la volatilité les atténue (Grobys et al., *Financial Markets and Portfolio Management*, 2025) | Taille des positions ∝ 1/volatilité + volatilité cible du portefeuille |
| Le momentum crypto est surtout un phénomène de grosses capitalisations | Univers limité aux 20 cryptos les plus liquides |
| La plupart des bots retail perdent de l'argent, surtout à cause de backtests sur-optimisés et des frais | Règles simples, réglage sur une période, test sur une autre, frais modélisés, bande de non-transaction |

## 4. Règles

1. **Univers point-in-time** : chaque jour, les 20 cryptos au plus fort volume médian sur 30 jours (USDT spot Binance), ≥ 180 jours d'historique, ≥ 10 M$/jour. La liste de candidats inclut des tokens morts ou délistés (LUNA, FTT, EOS, XMR, MATIC…) pour limiter le biais du survivant.
2. **Signal** : moyenne de 3 croisements d'EMA (8/32, 16/64, 32/128 jours), chacun divisé par la volatilité du prix et plafonné → « forecast » entre −2 et +2.
3. **Long ou cash** : achat seulement si forecast > 0. Pas de short, pas de levier.
4. **Sélection** : au plus 10 positions, les forecasts les plus forts.
5. **Taille** : poids ∝ forecast / volatilité, mis à l'échelle pour viser 50 % de volatilité annuelle (covariance 90 jours), réduits si les tendances sont faibles. Plafonds : 30 % par altcoin, 60 % BTC/ETH, 100 % au total.
6. **Régime** : si BTC < sa moyenne 200 jours → exposition ≤ 35 %, uniquement BTC/ETH.
7. **Exécution** : capital coupé en 6 tranches égales. Chaque tranche applique les règles sur sa propre « journée » (clôture à 00, 04, 08, 12, 16 ou 20 h UTC) : une décision toutes les 4 h, chacune sur 1/6 du capital (depuis le 24/11/2026, voir §11). On ne touche une ligne que si l'écart de poids dépasse 5 points.
8. **Coûts modélisés** : 0,10 % de frais + 0,05 % de slippage par ordre en backtest ; en paper, carnet bid/ask réel + 0,10 % + 0,05 %.

Testés puis **écartés** car non robustes : stop suiveur ATR, frein de drawdown (aidaient dans certaines combinaisons, nuisaient dans d'autres), signaux plus lents (64/256), univers de 10.

## 5. Validation

Réglage uniquement sur **juin 2018 → déc. 2022** (≈ 70 combinaisons testées). Test sur **janv. 2023 → nov. 2026**.

| | Bot | BTC buy & hold | Panier équipondéré top 10 |
|---|---|---|---|
| **Hors échantillon** — par an | +25,2 % | +39,8 % | +20,2 % |
| Hors échantillon — pire repli | −36,6 % | −53,0 % | −70,3 % |
| Hors échantillon — Sharpe | 0,86 | 0,95 | 0,61 |
| Hors échantillon — 6 mois, cas médian / 5 % pires | +5,7 % / −19,6 % | +20,2 % / −36,4 % | +5,4 % / −50,1 % |
| Complet 2018-2026 — par an | +33,9 % | +28,1 % | +17,8 % |
| Complet — pire repli | −36,6 % | −76,6 % | −79,3 % |
| Complet — Sharpe | 1,12 | 0,71 | 0,60 |

Rendements annuels du bot (BTC entre parenthèses) : 2018* −5 % (−51 %), 2019 +16 % (+94 %), 2020 +119 % (+302 %), 2021 +132 % (+60 %), 2022 −11 % (−64 %), 2023 +54 % (+156 %), 2024 +53 % (+121 %), 2025 0 % (−6 %), 2026* +2 % (−30 %).

Robustesse hors échantillon (variantes non sélectionnées) : Sharpe entre 0,57 et 0,92 selon les variantes ; avec des coûts doublés (0,30 %) le rendement passe de +25 % à +22 % par an.

**Transparence** : la volatilité cible (0,6 → 0,5) et la bande de non-transaction (3 → 5 points) ont été ajustées après une première lecture du test hors échantillon, en choisissant sur le plateau in-sample. Le test hors échantillon est donc légèrement contaminé. Première version : +21,2 %/an, −43,2 % de pire repli.

## 6. Lecture honnête

- Le bot **ne bat pas** le simple achat de BTC en rendement sur 2023-2026 (qui démarre près d'un creux). Il le bat sur la période complète et sur toutes les mesures de risque.
- Sur 6 mois, le résultat médian historique est modeste (+4 à +6 %). Les gros gains (+80 à +130 % dans les 5 % meilleurs cas) n'arrivent que si une tendance haussière forte démarre pendant la fenêtre.
- Le pire scénario typique sur 6 mois est d'environ −16 à −20 %. Le pire repli observé est de −37 % (marché sans direction de mars à octobre 2024, faux départs à répétition).
- Point de départ actuel : 100 % cash. Si le marché reste baissier ou sans tendance pendant 6 mois, le bot restera majoritairement en stablecoin et fera ≈ 0 %. C'est le comportement attendu.

## 7. Critères pendant le paper trading

| Indicateur | Normal | Alerte → on revoit |
|---|---|---|
| Écart paper vs backtest sur la même période | < 2 points / mois | > 5 points (bug d'exécution ou de données) |
| Repli depuis le sommet | < 25 % | > 40 % (au-delà du pire historique) |
| Rotation | 8-15× / an | > 25× (sur-trading) |
| Exécutions quotidiennes manquées | 0 | ≥ 2 d'affilée |

Décision « passage au réel » : pas avant 3 mois de paper cohérent, et sur une petite somme.

## 8. Pistes v2 (non implémentées, à tester sans toucher la v1)

- Petite poche short via contrats perpétuels en régime baissier : plus de rendement possible, mais risque de liquidation et funding.
- Rendement du cash : placer la part stablecoin dans un produit d'épargne en stablecoin (risque de contrepartie à évaluer).
- Filtre d'actualité via l'API Claude : écarté pour l'instant car impossible à backtester proprement.

## 9. Variantes « plus rapides / plus de cryptos » testées le 24/11/2026 (rejetées)

Même protocole : simulation 2019-09 → nov. 2026, réglage jugé sur 2019-09 → 2022-12, verdict sur 2023-01 → nov. 2026 (frais 0,15 %/ordre). Univers « large » = ~140 séries (memecoins, DeFi, IA, nouveaux tokens, tokens morts compris). Script : `scripts/compare.py`.

| Variante | Sharpe réglage | Sharpe test | Par an (test) | Pire repli (test) | Rotation/an |
|---|---|---|---|---|---|
| **v1 : journalier, 20 grandes cryptos** | 1,59 | **0,86** | **+25 %** | −37 % | 15× |
| Journalier, univers large | 1,31 | 0,68 | +18 % | −37 % | 16× |
| Journalier, univers large, top 30 | 1,39 | 0,79 | +23 % | −44 % | 16× |
| Journalier, régime assoupli (altcoins permis en marché baissier) | 1,14 | 0,90 | +27 % | −29 % | 15× |
| 4h, signaux rapides (1-21 j) | 1,68 | 0,87 | +25 % | −38 % | 57× |
| 4h, signaux très rapides (0,7-11 j) | **1,84** | 0,53 | +13 % | −49 % | 107× |
| 4h, univers large, rapides | 1,55 | 0,66 | +17 % | −41 % | 56× |
| 4h rapides, frais doublés | 1,53 | 0,61 | +15 % | −42 % | 57× |
| v1, frais doublés | 1,54 | 0,79 | +22 % | −38 % | 15× |

Conclusions :
- Plus c'est rapide, plus c'est beau sur la période de réglage et plus ça s'effondre sur la période de test : signature du sur-apprentissage. La version la plus rapide a le meilleur passé et le pire test.
- Le 4h « rapide » égale la v1 au mieux, avec 4× plus d'ordres : il perd tout avantage si les frais réels sont un peu plus élevés.
- Ajouter des memecoins et petits tokens dégrade les résultats partout (plus de bruit, faux départs, krachs).
- Le régime assoupli est meilleur en test mais nettement pire en réglage (−39 % de repli) : non robuste, non retenu.
- **Décision : la v1 reste en place.**

## 10. Poche short (vente à découverte via perpétuels) testée le 24/11/2026 (rejetée)

Accès : Binance a cessé ses services en France le 1er juillet 2026 (pas d'agrément MiCA). Les perpétuels restent accessibles aux particuliers français via des plateformes régulées MiFID II (Kraken, Coinbase), traités comme des CFD : levier limité à 2:1.

Test : shorts à levier 1x quand la tendance est négative, funding historique réel (archives Binance), frais 0,15 %/ordre. Réglage jugé sur 2020-01 → 2022-12, verdict sur 2023-01 → nov. 2026. Script : `scripts/shorts.py`.

| Variante | Sharpe réglage | Sharpe test | Par an (test) | Pire repli (test) | 2022 | 2026 |
|---|---|---|---|---|---|---|
| **v1 : long ou cash** | **1,72** | **0,86** | **+25 %** | −37 % | −11 % | +2 % |
| + short BTC/ETH ≤ 35 % (marché baissier) | 1,52 | 0,77 | +23 % | −40 % | +11 % | +5 % |
| + short BTC/ETH ≤ 60 % | 1,25 | 0,66 | +19 % | −44 % | +8 % | +7 % |
| + short top liquides ≤ 50 % | 1,53 | 0,60 | +16 % | −47 % | +20 % | +11 % |
| + short top liquides ≤ 100 % | 1,52 | 0,46 | +11 % | −50 % | +37 % | +7 % |
| Long/short permanent | 1,54 | 0,69 | +18 % | −29 % | +19 % | −5 % |

Conclusions : les shorts rapportent dans les longues baisses (2022, 2026) mais perdent davantage lors des rebonds brutaux et des marchés sans direction (2020, 2023-2024). Toutes les variantes font moins bien que la v1 sur les deux périodes. **Non retenu.** Le moteur garde l'option (`allow_short`, désactivée).

## 11. Fréquence et heure de décision, testées le 24/11/2026 → passage à 6 tranches

Scripts : `scripts/timing.py`, `scripts/tranches.py`. Même protocole (réglage 2019-09 → 2022-12, test 2023-01 → nov. 2026).

| Variante | Sharpe réglage | Sharpe test | Par an (test) | Pire repli (test) |
|---|---|---|---|---|
| 1 décision/jour à 01h Paris (v1) | 1,68 | 0,86 | +25 % | −37 % |
| 1 décision/jour à 05h Paris | **1,87** | 0,71 | +19 % | −37 % |
| 1 décision/jour à 09h Paris | 1,67 | 0,88 | +26 % | −34 % |
| 1 décision/jour à 13h Paris | 1,74 | 0,67 | +17 % | −34 % |
| 1 décision/jour à 17h Paris | 1,50 | 0,68 | +17 % | −38 % |
| 1 décision/jour à 21h Paris | 1,65 | 0,65 | +17 % | −37 % |
| Mêmes règles réévaluées toutes les 4 h (tout le capital) | 1,33 | 0,74 | +19 % | −36 % |
| **6 tranches, une décision toutes les 4 h** | **2,05** | **0,87** | +20 % | −36 % |

Constats :
- L'heure de décision change le résultat de ±5 points par an, sans logique : la meilleure heure sur la période de réglage (05h) est parmi les pires en test. C'est de la chance, pas un « meilleur moment ».
- Réévaluer tout le capital toutes les 4 h dégrade les résultats (plus d'allers-retours).
- Couper le capital en 6 tranches décalées supprime ce risque de « mauvaise heure » : Sharpe nettement supérieur sur le réglage et égal à la meilleure heure en test, contre 0,74 en moyenne pour une heure unique. Le rendement test (+20 %) est inférieur à celui de 01h, qui a simplement été l'une des heures chanceuses sur 2023-2026.
- **Décision : 6 tranches.** Le portefeuille paper (100 % cash au moment du changement) a été réparti en 6 parts égales.

## 12. Fiabilité de l'exécution (27/11/2026)

Le planificateur de GitHub Actions saute souvent des créneaux : 4 exécutions en 2 jours au lieu de 12. Corrections :
- Le workflow est lancé toutes les heures (idempotent).
- Chaque décision de tranche est exécutée au **prix du moment prévu**, c'est-à-dire l'ouverture de la bougie 5 min qui suit la fin de sa journée, même si le passage a lieu en retard.

Le paper trading reste ainsi fidèle à la stratégie.

## Sources

- Prix, volumes et historique : API publique Binance (data-api.binance.vision), CoinGecko `/global`, alternative.me Fear & Greed.
- [Coinpedia via Coinalyze — BTC à 62 000 $, bear market (nov. 2026)](https://coinalyze.net/bitcoin/usdc/deribit/btc_usdc-21nov26/price-chart-live/)
- [The Block — sorties des ETF bitcoin, 14/11/2026](https://theblock.co/news/markets/2026-11-14-bitcoin-etfs-lose-1-billion-for-the-second-week-in-a-row-as-crypto-funds-except-one-continue-bleeding-440337)
- [Vanguard — hausse de taux attendue en décembre 2026](https://corporate.vanguard.com/content/corporatesite/us/en/corp/articles/inflation-remains-front-center-as-fed-holds.html)
- [CoinMarketCap Academy — BTC −50 % depuis le record de 126 198 $](https://coinmarketcap.com/academy/article/whats-really-going-on-bitcoin-price-btc-four-year-cycle)
- [MEXC News — creux historiques 12-13 mois après le sommet](https://www.mexc.com/news/1021302)
- [Galaxy Research — creux de cycle estimé 40-46 k$](https://galaxy.com/insights/research/bitcoin-four-year-cycle-where-is-the-bottom)
- [Han, Kang & Ryu — Time-series and cross-sectional momentum in crypto under realistic assumptions](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
- [Gbadebo (2026) — Momentum trading in cryptocurrencies](https://www.journals.vu.lt/BATP/lt/article/download/44540/42590/138419)
- [Grobys et al. (2025) — Cryptocurrency momentum has (not) its moments](https://link.springer.com/article/10.1007/s11408-025-00474-9)
- [Liu, Tsyvinski & Wu — Common risk factors in cryptocurrency (NBER)](https://www.nber.org/papers/w25882.pdf)
- [Ramify — PFU crypto à 31,4 % en 2026](https://www.ramify.fr/crypto/fiscalite)
- R. Carver, *Systematic Trading* (2015) : scalaires de forecast EWMAC.

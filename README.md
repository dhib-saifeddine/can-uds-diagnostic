
### Tests

```bash
python3 -m pytest -q
```

The 48 unit tests need no CAN bus and run on every push through GitHub Actions. The scripts in `integration_manuelle/` are end-to-end checks to run by hand while both ECUs are running.

### Roadmap

Next step: **[can-secure-boot-secoc](https://github.com/dhib-saifeddine/can-secure-boot-secoc)** — extends this project with Secure Boot (firmware signature verification) and SecOC (authenticated CAN frames), aligned with ISO/SAE 21434.

### Author

Saif Eddine Dhib — embedded systems / IoT engineering, Tunisia.
[LinkedIn](https://www.linkedin.com/in/dhib-saif-eddine-09bb3942a) · [GitHub](https://github.com/dhib-saifeddine)

---

## Français

Simulation d'un réseau CAN automobile avec deux ECU (Moteur, Freinage) et un outil de diagnostic UDS (ISO 14229, via ISO-TP), sous Linux SocketCAN (`vcan0`).

### Pourquoi ce projet

Projet portfolio réalisé pour démontrer une maîtrise pratique des protocoles de communication et de diagnostic utilisés au quotidien dans l'embarqué automobile (CAN, UDS/ISO 14229) — des compétences recherchées pour des postes de développement ECU et d'outillage de diagnostic (Bosch, Continental, ZF, Hella, etc.).

### Architecture

Voir le schéma ci-dessus. Chaque ECU est un processus Python à deux threads partageant un état protégé par un verrou : un thread de diffusion périodique (trames encodées avec `cantools` et le fichier DBC `can_bus.dbc`, avec injection d'anomalies aléatoires qui génèrent des DTC) et un thread serveur UDS (requêtes via ISO-TP). Les identifiants sont ceux du tableau ci-dessus.

### Services UDS

Cinq services codés à la main sur les deux ECU : `0x10` changement de session, `0x19` lecture des DTC, `0x22` lecture de paramètres (RPM, température, pression), `0x11` reset (session étendue uniquement), `0x14` effacement des DTC.

### Robustesse

- Bus CAN absent au démarrage : message clair et arrêt propre.
- Bus perdu en cours de route : erreur signalée une seule fois, reconnexion automatique, message `Bus CAN rétabli.`
- Requête UDS malformée : réponse négative (NRC `0x13`), pas de plantage.
- Fermeture propre du bus à l'arrêt (`Ctrl+C`).

### Structure du projet

Voir l'arborescence ci-dessus (section anglaise).

### Installation et utilisation

Mêmes commandes que dans la section anglaise ci-dessus. SocketCAN n'existe que sous Linux.

### Tests

`python3 -m pytest -q` lance les 48 tests unitaires (sans bus CAN), exécutés à chaque push par GitHub Actions. Les scripts de `integration_manuelle/` se lancent à la main, ECU en marche.

### Prochaine étape

**[can-secure-boot-secoc](https://github.com/dhib-saifeddine/can-secure-boot-secoc)** — prolonge ce projet avec un Secure Boot (vérification de signature du firmware) et SecOC (authentification des trames CAN), conforme à l'esprit de l'ISO/SAE 21434.

### Auteur

Saif Eddine Dhib, ingénierie systèmes embarqués / IoT, Tunisie.
[LinkedIn](https://www.linkedin.com/in/dhib-saif-eddine-09bb3942a) · [GitHub](https://github.com/dhib-saifeddine)

# CAN Bus / UDS Diagnostic Simulator

[![Tests unitaires](https://github.com/dhib-saifeddine/can-uds-diagnostic/actions/workflows/tests.yml/badge.svg)](https://github.com/dhib-saifeddine/can-uds-diagnostic/actions/workflows/tests.yml)

🇬🇧 [English](#english) · 🇫🇷 [Français](#français)

---

## English

Simulation of an automotive CAN network with two ECUs (Engine, Brakes) and a UDS diagnostic tool (ISO 14229, over ISO-TP), running on Linux SocketCAN (`vcan0`).

### Architecture

```
[ECU Engine]  ──┐
                ├──►  Virtual CAN bus vcan0 (SocketCAN)  ◄──  [UDS diagnostic tool]
[ECU Brakes]  ──┘
```

Each ECU is one Python process with two threads sharing a lock-protected state:

- **Broadcast thread**: periodic CAN frames encoded with `cantools` and a DBC file (`can_bus.dbc`), plus random fault injection that raises DTCs.
- **UDS server thread**: answers diagnostic requests over ISO-TP.

| ECU | Request ID | Response ID | Broadcast frame |
|---|---|---|---|
| Engine | `0x7E0` | `0x7E8` | `0x100` (RPM, temperature) |
| Brakes | `0x7E1` | `0x7E9` | `0x200` (pressure, state) |

### UDS services (implemented by hand on both ECUs)

| SID | Service | Notes |
|---|---|---|
| `0x10` | DiagnosticSessionControl | default / extended |
| `0x19` | ReadDTCInformation | stored fault codes |
| `0x22` | ReadDataByIdentifier | `0xF190` RPM, `0xF191` engine temperature, `0xF1A0` brake pressure |
| `0x11` | ECUReset | hard / soft, extended session only |
| `0x14` | ClearDiagnosticInformation | clears DTCs |

### Robustness

- CAN bus missing at startup: clear message and clean exit.
- Bus lost while running (e.g. `vcan0` deleted): the error is reported once, the ECU reconnects automatically and prints `Bus CAN rétabli.`
- Malformed UDS request: negative response (NRC `0x13`) instead of a crash.
- Bus is always closed cleanly on exit (`Ctrl+C`).

### Installation

```bash
git clone https://github.com/dhib-saifeddine/can-uds-diagnostic.git
cd can-uds-diagnostic
python3 -m venv ~/can-env
source ~/can-env/bin/activate
pip install -r requirements.txt
```

SocketCAN exists on Linux only. Create the virtual bus:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

### Usage

Terminal 1: `python3 ecu_moteur.py` · Terminal 2: `python3 ecu_freinage.py` · Terminal 3:

```bash
python3 outil_diagnostic.py moteur session extended
python3 outil_diagnostic.py moteur read rpm
python3 outil_diagnostic.py moteur dtc
python3 outil_diagnostic.py freinage read pression
python3 outil_diagnostic.py moteur reset hard
python3 outil_diagnostic.py moteur clear
```

Example output:

```
Session changée avec succès : extended
rpm = 1347
1 code(s) défaut trouvé(s) :
  Code=0x010100  (Surchauffe moteur)
pression = 56
```

### Tests

```bash
python3 -m pytest -q
```

The 48 unit tests need no CAN bus and run on every push through GitHub Actions. The scripts in `integration_manuelle/` are end-to-end checks to run by hand while both ECUs are running.

### Author

Saif Eddine Dhib: embedded systems / IoT engineering, Tunisia.
[LinkedIn](https://www.linkedin.com/in/dhib-saif-eddine-09bb3942a) · [GitHub](https://github.com/dhib-saifeddine)

---

## Français

Simulation d'un réseau CAN automobile avec deux ECU (Moteur, Freinage) et un outil de diagnostic UDS (ISO 14229, via ISO-TP), sous Linux SocketCAN (`vcan0`).

### Architecture

Voir le schéma ci-dessus. Chaque ECU est un processus Python à deux threads partageant un état protégé par un verrou : un thread de diffusion périodique (trames encodées avec `cantools` et le fichier DBC `can_bus.dbc`, avec injection d'anomalies aléatoires qui génèrent des DTC) et un thread serveur UDS (requêtes via ISO-TP). Les identifiants sont ceux du tableau ci-dessus.

### Services UDS

Cinq services codés à la main sur les deux ECU : `0x10` changement de session, `0x19` lecture des DTC, `0x22` lecture de paramètres (RPM, température, pression), `0x11` reset (session étendue uniquement), `0x14` effacement des DTC.

### Robustesse

- Bus CAN absent au démarrage : message clair et arrêt propre.
- Bus perdu en cours de route : erreur signalée une seule fois, reconnexion automatique, message `Bus CAN rétabli.`
- Requête UDS malformée : réponse négative (NRC `0x13`), pas de plantage.
- Fermeture propre du bus à l'arrêt (`Ctrl+C`).

### Installation et utilisation

Mêmes commandes que dans la section anglaise ci-dessus. SocketCAN n'existe que sous Linux.

### Tests

`python3 -m pytest -q` lance les 48 tests unitaires (sans bus CAN), exécutés à chaque push par GitHub Actions. Les scripts de `integration_manuelle/` se lancent à la main, ECU en marche.

### Auteur

Saif Eddine Dhib, ingénierie systèmes embarqués / IoT, Tunisie.
[LinkedIn](https://www.linkedin.com/in/dhib-saif-eddine-09bb3942a) · [GitHub](https://github.com/dhib-saifeddine)

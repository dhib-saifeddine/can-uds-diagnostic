"""
ECU Moteur - Diffusion périodique + Serveur UDS (ISO 14229), même processus.

Ce script remplace les deux anciens scripts séparés ecu_moteur.py et
ecu_moteur_uds.py. Dans un vrai calculateur automobile, c'est le MÊME
firmware qui diffuse les mesures périodiques ET répond au diagnostic : les
deux rôles partagent donc le même état interne. Ici, EcuState (RPM,
température, codes défaut actifs) est partagé entre :

  - le thread "broadcast" : génère RPM/Température, encode et envoie la
    trame périodique 0x100 via cantools/DBC, et détecte les anomalies
    (déclenche les DTC correspondants) ;
  - le thread "UDS" : répond aux requêtes de diagnostic (changement de
    session, lecture des DTC détectés par le thread broadcast) sur
    ISO-TP (requêtes 0x7E0, réponses 0x7E8).

Gestion d'erreurs (étape 4 du cahier des charges) :
  - bus CAN indisponible au démarrage -> message clair + arrêt propre,
    au lieu d'une traceback brute ;
  - perte du bus en cours de fonctionnement (ex: vcan0 supprimée,
    recréée ou désactivée) -> chaque thread signale la perte UNE SEULE
    FOIS, puis tente de se reconnecter toutes les secondes et affiche
    "Bus CAN rétabli" au retour du bus ;
  - requête UDS corrompue / inattendue -> capturée et transformée en
    réponse négative UDS (NRC 0x13) au lieu de faire planter le thread
    serveur ;
  - arrêt par Ctrl+C -> les sockets CAN sont fermés proprement.

Deux connexions CAN distinctes (une par thread) sont utilisées par
simplicité et pour éviter tout partage d'objet non nécessaire ; c'est le
même bus vcan0 dans les deux cas.

Reconnexion : quand vcan0 est supprimée puis recréée, Linux lui attribue
un nouvel index d'interface et les anciens sockets deviennent inutilisables.
On mémorise donc l'index à l'ouverture et on le compare à chaque tour de
boucle pour détecter ce cas.
"""
import sys
import threading
import time
import random
import socket

import can
import cantools
import isotp

from uds_ecu_moteur_service import (
    EcuState,
    handle_request,
    record_anomaly,
    clear_transient_status,
    DTC_ENGINE_OVERHEAT,
    DTC_ENGINE_STALLED,
)

CHANNEL = "vcan0"
DBC_PATH = "can_bus.dbc"
BROADCAST_MESSAGE_NAME = "ECU_Moteur"
ANOMALY_PROBABILITY = 0.05
RECONNECT_DELAY = 1.0  # secondes entre deux tentatives de reconnexion

REQUEST_ID = 0x7E0   # Outil de diagnostic -> ECU Moteur
RESPONSE_ID = 0x7E8  # ECU Moteur -> Outil de diagnostic


def interface_index(channel: str):
    """Retourne l'index de l'interface si elle existe ET est active (UP),
    sinon None. Un nouvel index = interface recréée = anciens sockets morts."""
    try:
        index = socket.if_nametoindex(channel)
        with open(f"/sys/class/net/{channel}/flags") as f:
            flags = int(f.read().strip(), 16)
    except (OSError, ValueError):
        return None
    if not flags & 0x1:  # IFF_UP
        return None
    return index


def open_bus(channel: str) -> can.BusABC:
    return can.interface.Bus(channel=channel, interface="socketcan")


def close_bus(bus) -> None:
    """Ferme un bus sans jamais lever d'exception."""
    try:
        bus.shutdown()
    except Exception:
        pass


def create_bus(channel: str) -> can.BusABC:
    """Ouvre le bus CAN au démarrage, avec message clair si l'interface
    n'existe pas ou n'est pas active (cas : vcan0 non recréée après reboot)."""
    try:
        return open_bus(channel)
    except (OSError, can.CanError) as e:
        print(f"[ERREUR] Impossible d'ouvrir le bus CAN '{channel}' : {e}")
        print("Vérifiez que l'interface existe et est active :")
        print(f"  sudo ip link add dev {channel} type vcan   (si elle n'existe pas)")
        print(f"  sudo ip link set up {channel}")
        sys.exit(1)


def wait_for_bus(channel: str, tag: str, old_bus, reason: str):
    """Ferme l'ancien bus, signale la perte UNE fois, puis attend le retour
    du bus. Retourne (nouveau_bus, nouvel_index)."""
    close_bus(old_bus)
    print(f"[{tag}] Bus CAN '{channel}' perdu ({reason}). "
          f"Reconnexion automatique toutes les {RECONNECT_DELAY:.0f}s...")

    while True:
        time.sleep(RECONNECT_DELAY)
        index = interface_index(channel)
        if index is None:
            continue
        try:
            bus = open_bus(channel)
        except (OSError, can.CanError):
            continue
        print(f"[{tag}] Bus CAN rétabli.")
        return bus, index


def broadcast_loop(bus: can.BusABC, db: cantools.database.Database, state: EcuState) -> None:
    """Diffuse RPM/Température toutes les secondes et met à jour les DTC."""
    message = db.get_message_by_name(BROADCAST_MESSAGE_NAME)
    print(f"[Broadcast] Diffusion périodique sur {CHANNEL} (ID=0x{message.frame_id:X})...")

    index = interface_index(CHANNEL)

    try:
        while True:
            # Détection d'une interface supprimée / recréée / désactivée
            if interface_index(CHANNEL) != index:
                bus, index = wait_for_bus(
                    CHANNEL, "Broadcast", bus, "interface disparue, recréée ou désactivée"
                )

            anomaly = random.random() < ANOMALY_PROBABILITY

            if anomaly and random.random() < 0.5:
                rpm, temperature = random.randint(800, 4000), random.randint(111, 130)
            elif anomaly:
                rpm, temperature = 0, random.randint(70, 110)
            else:
                rpm, temperature = random.randint(800, 4000), random.randint(70, 110)

            with state.lock:
                state.rpm = rpm
                state.temperature = temperature

                if temperature > 110:
                    record_anomaly(state, DTC_ENGINE_OVERHEAT)
                else:
                    clear_transient_status(state, DTC_ENGINE_OVERHEAT)

                if rpm == 0:
                    record_anomaly(state, DTC_ENGINE_STALLED)
                else:
                    clear_transient_status(state, DTC_ENGINE_STALLED)

            data = message.encode({"RPM": rpm, "Temperature": temperature}, strict=False)

            try:
                bus.send(can.Message(arbitration_id=message.frame_id, data=data, is_extended_id=False))
            except (can.CanError, OSError) as e:
                bus, index = wait_for_bus(CHANNEL, "Broadcast", bus, str(e))
                continue

            tag = " [ANOMALIE]" if anomaly else ""
            print(f"[Broadcast] Envoyé: RPM={rpm}, Temp={temperature}°C{tag}")

            time.sleep(1)
    finally:
        close_bus(bus)


def start_isotp_stack(bus: can.BusABC) -> isotp.CanStack:
    address = isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=REQUEST_ID, txid=RESPONSE_ID)
    stack = isotp.CanStack(bus=bus, address=address)
    stack.start()
    return stack


def stop_isotp_stack(stack) -> None:
    """Arrête la pile ISO-TP sans jamais lever d'exception."""
    try:
        stack.stop()
    except Exception:
        pass


def uds_server_loop(bus: can.BusABC, state: EcuState, stop_event: threading.Event) -> None:
    """Répond aux requêtes UDS reçues sur 0x7E0, en s'appuyant sur l'état partagé."""
    index = interface_index(CHANNEL)
    stack = start_isotp_stack(bus)

    print(f"[UDS] Serveur démarré (requêtes sur 0x{REQUEST_ID:X}, réponses sur 0x{RESPONSE_ID:X})")

    try:
        while not stop_event.is_set():
            try:
                # Interface supprimée / recréée : les sockets sont périmés
                if interface_index(CHANNEL) != index:
                    raise OSError("interface disparue, recréée ou désactivée")
                request = stack.recv(block=True, timeout=1.0)
            except Exception as e:
                # Bus perdu ou erreur ISO-TP inattendue : on signale UNE fois,
                # puis on attend le bus et on recrée pile ISO-TP + socket.
                stop_isotp_stack(stack)
                bus, index = wait_for_bus(CHANNEL, "UDS", bus, str(e))
                stack = start_isotp_stack(bus)
                continue

            if request is None:
                # Timeout normal : personne n'a envoyé de requête, on reboucle.
                continue

            print(f"[UDS] Requête reçue : {bytes(request).hex()}")

            with state.lock:
                try:
                    response = handle_request(bytes(request), state)
                except Exception as e:
                    # Requête corrompue / inattendue : on ne laisse jamais
                    # une exception remonter et tuer le thread serveur.
                    print(f"[UDS] ERREUR interne lors du traitement de la requête : {e}")
                    sid = request[0] if len(request) > 0 else 0x00
                    response = bytes([0x7F, sid, 0x13])  # NRC: incorrectMessageLengthOrInvalidFormat

            try:
                stack.send(response)
                print(f"[UDS] Réponse envoyée : {response.hex()}")
            except Exception as e:
                print(f"[UDS] ERREUR lors de l'envoi de la réponse : {e}")
    finally:
        stop_isotp_stack(stack)
        close_bus(bus)


def main():
    db = cantools.database.load_file(DBC_PATH)
    state = EcuState()
    stop_event = threading.Event()

    broadcast_bus = create_bus(CHANNEL)
    uds_bus = create_bus(CHANNEL)

    uds_thread = threading.Thread(
        target=uds_server_loop, args=(uds_bus, state, stop_event), daemon=True
    )
    uds_thread.start()

    try:
        broadcast_loop(broadcast_bus, db, state)
    except KeyboardInterrupt:
        print("\nArrêt de l'ECU Moteur.")
    finally:
        # Laisse le thread UDS fermer proprement sa pile ISO-TP et son bus
        stop_event.set()
        uds_thread.join(timeout=3.0)


if __name__ == "__main__":
    main()

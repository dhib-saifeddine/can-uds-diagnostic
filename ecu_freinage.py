"""
ECU Freinage - Diffusion périodique + Serveur UDS (ISO 14229), même processus.

Gestion d'erreurs (étape 4 du cahier des charges) :
  - bus CAN indisponible au démarrage -> message clair + arrêt propre ;
  - perte du bus en cours de fonctionnement (ex: vcan0 supprimée,
    recréée ou désactivée) -> chaque thread signale la perte UNE SEULE
    FOIS, puis tente de se reconnecter toutes les secondes et affiche
    "Bus CAN rétabli" au retour du bus ;
  - requête UDS corrompue / inattendue -> capturée et transformée en
    réponse négative UDS (NRC 0x13) au lieu de faire planter le thread
    serveur ;
  - arrêt par Ctrl+C -> les sockets CAN / ISO-TP sont fermés proprement.

Reconnexion : quand vcan0 est supprimée puis recréée, Linux lui attribue
un nouvel index d'interface et les anciens sockets deviennent inutilisables.
On mémorise donc l'index à l'ouverture et on le compare à chaque tour de
boucle pour détecter ce cas.
"""
import socket
import sys
import can
import time
import random
import threading
import isotp

from uds_ecu_freinage_service import (
    EcuFreinageState,
    process_uds_request,
    DTC_BRAKE_PRESSURE_LOW,
    DTC_BRAKE_WEAR,
)

CHANNEL = "vcan0"
RECONNECT_DELAY = 1.0  # secondes entre deux tentatives de reconnexion

REQUEST_ID = 0x7E1   # Outil de diagnostic -> ECU Freinage
RESPONSE_ID = 0x7E9  # ECU Freinage -> Outil de diagnostic

state = EcuFreinageState()


# ---------------------------------------------------------------------------
# Outils communs : détection de l'interface, ouverture / fermeture des sockets
# ---------------------------------------------------------------------------

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


def open_isotp_socket() -> "isotp.socket":
    """Ouvre un socket ISO-TP lié à vcan0 (peut lever OSError)."""
    s = isotp.socket()
    addr = isotp.Address(
        isotp.AddressingMode.Normal_11bits, txid=RESPONSE_ID, rxid=REQUEST_ID
    )
    try:
        s.bind(CHANNEL, addr)
    except OSError:
        s.close()
        raise
    s.settimeout(1.0)  # timeout périodique : permet de détecter un bus perdu
    return s


def close_isotp_socket(s) -> None:
    """Ferme un socket ISO-TP sans jamais lever d'exception."""
    try:
        s.close()
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


def create_isotp_socket() -> "isotp.socket":
    """Ouvre le socket ISO-TP au démarrage, avec message clair en cas d'échec."""
    try:
        return open_isotp_socket()
    except OSError as e:
        print(f"[UDS] ERREUR : impossible de se lier au bus '{CHANNEL}' : {e}")
        print("Vérifiez que l'interface existe et est active :")
        print(f"  sudo ip link add dev {CHANNEL} type vcan   (si elle n'existe pas)")
        print(f"  sudo ip link set up {CHANNEL}")
        sys.exit(1)


def wait_for_bus(tag: str, old_bus, reason: str, stop_event: threading.Event):
    """Ferme l'ancien bus, signale la perte UNE fois, puis attend le retour
    du bus. Retourne (nouveau_bus, nouvel_index), ou (None, None) si l'arrêt
    du programme est demandé pendant l'attente."""
    close_bus(old_bus)
    print(f"[{tag}] Bus CAN '{CHANNEL}' perdu ({reason}). "
          f"Reconnexion automatique toutes les {RECONNECT_DELAY:.0f}s...")

    while not stop_event.wait(RECONNECT_DELAY):
        index = interface_index(CHANNEL)
        if index is None:
            continue
        try:
            bus = open_bus(CHANNEL)
        except (OSError, can.CanError):
            continue
        print(f"[{tag}] Bus CAN rétabli.")
        return bus, index
    return None, None


def wait_for_isotp_socket(tag: str, old_socket, reason: str, stop_event: threading.Event):
    """Ferme l'ancien socket ISO-TP, signale la perte UNE fois, puis attend le
    retour du bus. Retourne (nouveau_socket, nouvel_index), ou (None, None)
    si l'arrêt du programme est demandé pendant l'attente."""
    close_isotp_socket(old_socket)
    print(f"[{tag}] Bus CAN '{CHANNEL}' perdu ({reason}). "
          f"Reconnexion automatique toutes les {RECONNECT_DELAY:.0f}s...")

    while not stop_event.wait(RECONNECT_DELAY):
        index = interface_index(CHANNEL)
        if index is None:
            continue
        try:
            s = open_isotp_socket()
        except OSError:
            continue
        print(f"[{tag}] Bus CAN rétabli.")
        return s, index
    return None, None


# ---------------------------------------------------------------------------
# Thread 1 : diffusion périodique
# ---------------------------------------------------------------------------

def broadcast_thread(bus: can.BusABC, stop_event: threading.Event) -> None:
    print(f"[Broadcast] ECU Freinage démarrée sur {CHANNEL}...")
    index = interface_index(CHANNEL)

    try:
        while not stop_event.is_set():
            # Détection d'une interface supprimée / recréée / désactivée
            if interface_index(CHANNEL) != index:
                bus, index = wait_for_bus(
                    "Broadcast", bus, "interface disparue, recréée ou désactivée", stop_event
                )
                if bus is None:
                    break

            with state.lock:
                pression = random.randint(0, 100)
                anomalie = random.random() < 0.05

                if anomalie:
                    if random.choice([True, False]):
                        state.dtcs[DTC_BRAKE_PRESSURE_LOW] = {
                            "confirmed": True, "test_failed_this_cycle": True
                        }
                        pression = random.randint(0, 20)
                    else:
                        state.dtcs[DTC_BRAKE_WEAR] = {
                            "confirmed": True, "test_failed_this_cycle": True
                        }
                    state.etat = 1
                else:
                    state.etat = 0

                state.pression = pression
                etat_actuel = state.etat

            data = [pression, etat_actuel, 0, 0, 0, 0, 0, 0]
            msg = can.Message(arbitration_id=0x200, data=data, is_extended_id=False)

            try:
                bus.send(msg)
            except (can.CanError, OSError) as e:
                bus, index = wait_for_bus("Broadcast", bus, str(e), stop_event)
                if bus is None:
                    break
                continue

            tag = " [ANOMALIE]" if anomalie else ""
            print(f"[Broadcast] Envoyé: Pression={pression}%, État={etat_actuel}{tag}")

            stop_event.wait(1.5)
    finally:
        close_bus(bus)


# ---------------------------------------------------------------------------
# Thread 2 : serveur UDS
# ---------------------------------------------------------------------------

def uds_server_thread(s, stop_event: threading.Event) -> None:
    index = interface_index(CHANNEL)

    print(f"[UDS] Serveur démarré (requêtes sur 0x{REQUEST_ID:X}, réponses sur 0x{RESPONSE_ID:X})")

    try:
        while not stop_event.is_set():
            try:
                # Interface supprimée / recréée : le socket est périmé
                if interface_index(CHANNEL) != index:
                    raise OSError("interface disparue, recréée ou désactivée")
                request = s.recv()
            except socket.timeout:
                # Rien reçu dans le délai : normal, on reboucle.
                continue
            except OSError as e:
                # Bus perdu : on signale UNE fois, puis on attend le bus
                # et on recrée le socket ISO-TP.
                s, index = wait_for_isotp_socket("UDS", s, str(e), stop_event)
                if s is None:
                    break
                continue

            if not request:
                continue

            try:
                response = process_uds_request(request, state)
            except Exception as e:
                # Requête corrompue / inattendue : on ne laisse jamais une
                # exception remonter et tuer le thread serveur.
                print(f"[UDS] ERREUR interne lors du traitement de la requête : {e}")
                sid = request[0] if len(request) > 0 else 0x00
                response = bytes([0x7F, sid, 0x13])  # NRC: incorrectMessageLengthOrInvalidFormat

            try:
                s.send(response)
            except OSError as e:
                print(f"[UDS] ERREUR lors de l'envoi de la réponse : {e}")
    finally:
        close_isotp_socket(s)


def main():
    stop_event = threading.Event()

    # Ouverture dans le thread principal : si le bus est absent, message
    # clair et arrêt immédiat de tout le programme.
    bus = create_bus(CHANNEL)
    isotp_socket = create_isotp_socket()

    t1 = threading.Thread(target=broadcast_thread, args=(bus, stop_event), daemon=True)
    t2 = threading.Thread(target=uds_server_thread, args=(isotp_socket, stop_event), daemon=True)

    t1.start()
    t2.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt de l'ECU Freinage.")
    finally:
        # Laisse les threads fermer proprement leurs sockets
        stop_event.set()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)


if __name__ == "__main__":
    main()

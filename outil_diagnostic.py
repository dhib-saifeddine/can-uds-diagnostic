"""
Outil de diagnostic - Client UDS (ISO 14229) via ISO-TP / vcan0.

Supporte les deux ECU (Moteur, Freinage) avec gestion d'erreurs robuste :
- bus CAN indisponible au démarrage -> message clair, arrêt propre
- coupure du bus en cours de dialogue -> message clair, pas de plantage
- requête/réponse malformée ou inattendue -> gérée proprement
- le bus CAN est toujours fermé proprement en fin de commande

Usage :
    python3 outil_diagnostic.py <ecu> session [default|extended]
    python3 outil_diagnostic.py <ecu> dtc
    python3 outil_diagnostic.py <ecu> clear
    python3 outil_diagnostic.py <ecu> read <parametre>
    python3 outil_diagnostic.py <ecu> reset [hard|soft]

<ecu> : moteur | freinage
"""
import struct
import sys
from contextlib import contextmanager

import can
import isotp
import udsoncan
from udsoncan.client import Client
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.exceptions import NegativeResponseException, TimeoutException
from udsoncan.services import DiagnosticSessionControl, ECUReset

SESSIONS = {
    "default": DiagnosticSessionControl.Session.defaultSession,
    "extended": DiagnosticSessionControl.Session.extendedDiagnosticSession,
}

RESET_TYPES = {
    "hard": ECUReset.ResetType.hardReset,
    "soft": ECUReset.ResetType.softReset,
}

ECU_CONFIG = {
    "moteur": {
        "request_id": 0x7E0,
        "response_id": 0x7E8,
        "dtc_names": {
            0x010100: "Surchauffe moteur",
            0x010200: "Moteur calé",
        },
        "dids": {"rpm": 0xF190, "temperature": 0xF191},
        "did_formats": {0xF190: ">H", 0xF191: ">B"},
    },
    "freinage": {
        "request_id": 0x7E1,
        "response_id": 0x7E9,
        "dtc_names": {
            0x020100: "Fuite de pression",
            0x020200: "Usure des plaquettes",
        },
        "dids": {"pression": 0xF1A0},
        "did_formats": {0xF1A0: ">B"},
    },
}


@contextmanager
def uds_client(ecu: str):
    """Ouvre le bus CAN et un client UDS, puis ferme TOUT proprement.

    Utilisation :
        with uds_client("moteur") as client:
            client.read_data_by_identifier(...)
    """
    config = ECU_CONFIG[ecu]

    try:
        bus = can.interface.Bus(channel="vcan0", interface="socketcan")
    except (OSError, can.CanError) as e:
        print("Erreur : impossible d'accéder au bus CAN 'vcan0'.")
        print(f"Détail : {e}")
        print("Vérifiez que l'interface existe et est active :")
        print("  sudo ip link add dev vcan0 type vcan   (si elle n'existe pas)")
        print("  sudo ip link set up vcan0")
        sys.exit(1)

    try:
        address = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            rxid=config["response_id"],
            txid=config["request_id"],
        )
        isotp_layer = isotp.CanStack(bus=bus, address=address)
        conn = PythonIsoTpConnection(isotp_layer)

        client_config = udsoncan.configs.default_client_config.copy()
        client_config["data_identifiers"] = config["did_formats"]

        client = Client(conn, request_timeout=2, config=client_config)
        with client:
            yield client
    finally:
        # Fermeture propre du bus : supprime le message
        # "SocketcanBus was not properly shut down"
        try:
            bus.shutdown()
        except Exception:
            pass


def cmd_session(ecu: str, session_key: str) -> None:
    if session_key not in SESSIONS:
        print("Usage : python3 outil_diagnostic.py <ecu> session [default|extended]")
        sys.exit(1)

    with uds_client(ecu) as client:
        try:
            response = client.change_session(SESSIONS[session_key])
            print(f"Session changée avec succès : {session_key}")
            print(f"Réponse brute : {response.original_payload.hex()}")
        except NegativeResponseException as e:
            print(f"Réponse négative de l'ECU : code={e.response.code_name}")
        except TimeoutException:
            print("Pas de réponse de l'ECU (timeout). Le serveur UDS est-il lancé ?")
        except (OSError, can.CanError) as e:
            print("Erreur : le bus CAN est devenu indisponible pendant l'échange.")
            print(f"Détail : {e}")


def cmd_dtc(ecu: str) -> None:
    dtc_names = ECU_CONFIG[ecu]["dtc_names"]

    with uds_client(ecu) as client:
        try:
            response = client.get_dtc_by_status_mask(0xFF)

            if not response.service_data.dtcs:
                print("Aucun code défaut actif.")
                return

            print(f"{len(response.service_data.dtcs)} code(s) défaut trouvé(s) :\n")
            for dtc in response.service_data.dtcs:
                nom = dtc_names.get(dtc.id, "Code inconnu")
                status = dtc.status
                print(f"  Code=0x{dtc.id:06X}  ({nom})")
                print(
                    f"    confirmed={status.confirmed}  "
                    f"test_failed_this_cycle={status.test_failed_this_operation_cycle}"
                )

        except NegativeResponseException as e:
            print(f"Réponse négative de l'ECU : code={e.response.code_name}")
        except TimeoutException:
            print("Pas de réponse de l'ECU (timeout). Le serveur UDS est-il lancé ?")
        except (OSError, can.CanError) as e:
            print("Erreur : le bus CAN est devenu indisponible pendant l'échange.")
            print(f"Détail : {e}")


def cmd_clear(ecu: str) -> None:
    with uds_client(ecu) as client:
        try:
            client.clear_dtc()
            print("Codes défaut effacés avec succès.")
        except NegativeResponseException as e:
            print(f"Réponse négative de l'ECU : code={e.response.code_name}")
        except TimeoutException:
            print("Pas de réponse de l'ECU (timeout). Le serveur UDS est-il lancé ?")
        except (OSError, can.CanError) as e:
            print("Erreur : le bus CAN est devenu indisponible pendant l'échange.")
            print(f"Détail : {e}")


def cmd_read(ecu: str, parametre: str) -> None:
    dids = ECU_CONFIG[ecu]["dids"]

    if parametre not in dids:
        print(f"Paramètre inconnu : {parametre}")
        print(f"Paramètres disponibles pour '{ecu}' : {', '.join(dids.keys())}")
        sys.exit(1)

    did = dids[parametre]

    with uds_client(ecu) as client:
        try:
            response = client.read_data_by_identifier(did)
            valeur = response.service_data.values[did]
            if isinstance(valeur, tuple):
                valeur = valeur[0]
            print(f"{parametre} = {valeur}")
        except NegativeResponseException as e:
            print(f"Réponse négative de l'ECU : code={e.response.code_name}")
        except TimeoutException:
            print("Pas de réponse de l'ECU (timeout). Le serveur UDS est-il lancé ?")
        except (OSError, can.CanError) as e:
            print("Erreur : le bus CAN est devenu indisponible pendant l'échange.")
            print(f"Détail : {e}")
        except (struct.error, KeyError, IndexError) as e:
            print("Erreur : réponse de l'ECU mal formée ou inattendue, impossible de la décoder.")
            print(f"Détail : {e}")


def cmd_reset(ecu: str, reset_key: str) -> None:
    if reset_key not in RESET_TYPES:
        print("Usage : python3 outil_diagnostic.py <ecu> reset [hard|soft]")
        sys.exit(1)

    with uds_client(ecu) as client:
        try:
            client.ecu_reset(RESET_TYPES[reset_key])
            print(f"Reset {reset_key} effectué avec succès sur l'ECU {ecu}.")
        except NegativeResponseException as e:
            print(f"Réponse négative de l'ECU : code={e.response.code_name}")
        except TimeoutException:
            print("Pas de réponse de l'ECU (timeout). Le serveur UDS est-il lancé ?")
        except (OSError, can.CanError) as e:
            print("Erreur : le bus CAN est devenu indisponible pendant l'échange.")
            print(f"Détail : {e}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    ecu = sys.argv[1]
    command = sys.argv[2]

    if ecu not in ECU_CONFIG:
        print(f"ECU inconnue : {ecu}")
        print(f"ECU disponibles : {', '.join(ECU_CONFIG.keys())}")
        sys.exit(1)

    if command == "session":
        if len(sys.argv) != 4:
            print("Usage : python3 outil_diagnostic.py <ecu> session [default|extended]")
            sys.exit(1)
        cmd_session(ecu, sys.argv[3])

    elif command == "dtc":
        cmd_dtc(ecu)

    elif command == "clear":
        cmd_clear(ecu)

    elif command == "read":
        if len(sys.argv) != 4:
            print("Usage : python3 outil_diagnostic.py <ecu> read <parametre>")
            sys.exit(1)
        cmd_read(ecu, sys.argv[3])

    elif command == "reset":
        if len(sys.argv) != 4:
            print("Usage : python3 outil_diagnostic.py <ecu> reset [hard|soft]")
            sys.exit(1)
        cmd_reset(ecu, sys.argv[3])

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.")
        sys.exit(1)

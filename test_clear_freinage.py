"""
Script de test manuel pour valider le service ClearDiagnosticInformation
(SID 0x14) de l'ECU Freinage en conditions réelles (VM avec vcan0).

Usage : python3 test_clear_freinage.py
"""
import socket
from typing import Optional

import isotp

REQUEST_ID = 0x7E1   # Outil de diagnostic -> ECU Freinage
RESPONSE_ID = 0x7E9  # ECU Freinage -> Outil de diagnostic


def send_request(payload: bytes, timeout: float = 2.0) -> Optional[bytes]:
    s = isotp.socket()
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=REQUEST_ID, rxid=RESPONSE_ID)
    s.bind('vcan0', addr)
    s.settimeout(timeout)

    try:
        s.send(payload)
        response = s.recv()
        return bytes(response) if response else None
    except socket.timeout:
        return None
    finally:
        s.close()


def show(label: str, response: Optional[bytes]) -> None:
    if response is None:
        print(f"{label} : AUCUNE RÉPONSE (timeout)")
    else:
        print(f"{label} : {response.hex()}")


def main():
    print("1. Lecture des DTC actuels")
    show("   Réponse", send_request(bytes([0x19, 0x02, 0xFF])))

    print("\n2. Effacement de tous les DTC (groupe 0xFFFFFF, réponse attendue 54)")
    show("   Réponse", send_request(bytes([0x14, 0xFF, 0xFF, 0xFF])))

    print("\n3. Relecture des DTC (vide, ou ré-apparu si l'anomalie est toujours en cours)")
    show("   Réponse", send_request(bytes([0x19, 0x02, 0xFF])))


if __name__ == "__main__":
    main()

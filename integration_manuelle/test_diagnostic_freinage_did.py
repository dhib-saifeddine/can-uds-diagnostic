import isotp
import time

def lire_pression():
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E1, rxid=0x7E9)
    s = isotp.socket(timeout=2.0)
    s.bind('vcan0', addr)

    request = bytes([0x22, 0xF1, 0xA0])
    s.send(request)
    time.sleep(0.3)
    response = s.recv()

    if response and response[0] == 0x62:
        pression = response[3]
        print(f"✅ Pression de freinage : {pression}%")
    elif response and response[0] == 0x7F:
        print(f"❌ Réponse négative, NRC=0x{response[2]:02X}")
    else:
        print("❌ Aucune réponse (timeout)")

    s.close()


lire_pression()

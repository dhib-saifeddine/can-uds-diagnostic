import isotp
import time

def lire_did(nom, did_high, did_low):
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E0, rxid=0x7E8)
    s = isotp.socket(timeout=2.0)
    s.bind('vcan0', addr)

    request = bytes([0x22, did_high, did_low])
    s.send(request)
    time.sleep(0.3)
    response = s.recv()

    if response and response[0] == 0x62:
        if did_high == 0xF1 and did_low == 0x90:  # RPM
            valeur = (response[3] << 8) | response[4]
            print(f"✅ {nom} : {valeur} tr/min")
        elif did_high == 0xF1 and did_low == 0x91:  # Température
            valeur = response[3]
            print(f"✅ {nom} : {valeur}°C")
    elif response and response[0] == 0x7F:
        print(f"❌ {nom} : réponse négative, NRC=0x{response[2]:02X}")
    else:
        print(f"❌ {nom} : aucune réponse (timeout)")

    s.close()


lire_did("RPM moteur", 0xF1, 0x90)
time.sleep(0.5)
lire_did("Température moteur", 0xF1, 0x91)

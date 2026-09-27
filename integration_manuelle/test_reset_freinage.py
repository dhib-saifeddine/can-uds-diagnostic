import isotp
import time

def envoyer(nom, request):
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E1, rxid=0x7E9)
    s = isotp.socket(timeout=2.0)
    s.bind('vcan0', addr)
    s.send(request)
    time.sleep(0.3)
    response = s.recv()
    s.close()
    print(f"{nom} : {response.hex() if response else 'timeout'}")
    return response


# 1. Reset refusé en session par défaut
envoyer("Reset (session par défaut, doit échouer)", bytes([0x11, 0x01]))

time.sleep(0.5)

# 2. Passage en session étendue
envoyer("Passage session étendue", bytes([0x10, 0x03]))

time.sleep(0.5)

# 3. Reset accepté cette fois
envoyer("Reset hardReset (doit réussir)", bytes([0x11, 0x01]))

time.sleep(0.5)

# 4. Lecture pression après reset (doit être proche de 100%)
envoyer("Lecture pression après reset", bytes([0x22, 0xF1, 0xA0]))

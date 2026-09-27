import isotp
import time

addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E1, rxid=0x7E9)
s = isotp.socket(timeout=2.0)
s.bind('vcan0', addr)

# Requête ReadDTCInformation (SID 0x19), sous-fonction reportDTCByStatusMask (0x02), masque 0xFF
request = bytes([0x19, 0x02, 0xFF])
s.send(request)

time.sleep(0.5)
response = s.recv()

if response:
    print(f"Réponse brute : {response.hex()}")
    if response[0] == 0x59:
        print("✅ Réponse positive UDS reçue")
        nb_dtc = (len(response) - 3) // 4
        if nb_dtc == 0:
            print("Aucun code défaut actif")
        else:
            print(f"{nb_dtc} code(s) défaut trouvé(s) :")
            for i in range(nb_dtc):
                offset = 3 + i * 4
                code = int.from_bytes(response[offset:offset+3], 'big')
                status = response[offset+3]
                print(f"  Code=0x{code:06X}  status=0x{status:02X}")
    elif response[0] == 0x7F:
        print(f"❌ Réponse négative, NRC=0x{response[2]:02X}")
else:
    print("❌ Aucune réponse reçue (timeout)")

s.close()

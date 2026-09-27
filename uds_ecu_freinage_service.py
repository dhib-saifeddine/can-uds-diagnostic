import threading

# Codes défaut spécifiques au freinage
DTC_BRAKE_PRESSURE_LOW = 0x020100   # Fuite de pression détectée
DTC_BRAKE_WEAR = 0x020200            # Usure des plaquettes

class EcuFreinageState:
    """État partagé entre le thread broadcast et le thread serveur UDS."""
    def __init__(self):
        self.lock = threading.Lock()
        self.pression = 100
        self.etat = 0  # 0 = OK, 1 = usure
        self.dtcs = {}  # {code: {"confirmed": bool, "test_failed_this_cycle": bool}}
        self.session = 0x01  # defaultSession

    def set_anomalie(self, code):
        with self.lock:
            self.dtcs[code] = {"confirmed": True, "test_failed_this_cycle": True}

    def get_dtcs(self):
        with self.lock:
            return dict(self.dtcs)


def handle_diagnostic_session_control(request_data, state):
    """SID 0x10 - identique à la logique Moteur."""
    if len(request_data) < 1:
        return bytes([0x7F, 0x10, 0x13])  # NRC: incorrectMessageLengthOrInvalidFormat

    sub_function = request_data[0]
    if sub_function not in (0x01, 0x03):
        return bytes([0x7F, 0x10, 0x12])  # NRC: subFunctionNotSupported

    with state.lock:
        state.session = sub_function

    return bytes([0x50, sub_function, 0x00, 0x32, 0x01, 0xF4])


def handle_read_dtc_information(request_data, state):
    """SID 0x19, sous-fonction 0x02 (reportDTCByStatusMask)."""
    if len(request_data) < 2:
        return bytes([0x7F, 0x19, 0x13])

    sub_function = request_data[0]
    if sub_function != 0x02:
        return bytes([0x7F, 0x19, 0x12])

    status_mask = request_data[1]
    dtcs = state.get_dtcs()

    response = bytes([0x59, 0x02, status_mask])
    for code, info in dtcs.items():
        status_byte = 0x00
        if info["confirmed"]:
            status_byte |= 0x08
        if info["test_failed_this_cycle"]:
            status_byte |= 0x01
        response += code.to_bytes(3, 'big') + bytes([status_byte])

    return response


# --- ReadDataByIdentifier (0x22) ---
DID_BRAKE_PRESSURE = 0xF1A0

SUPPORTED_DIDS = {
    DID_BRAKE_PRESSURE: 1,  # Pression sur 1 octet
}


def handle_read_data_by_identifier(request_data, state):
    if len(request_data) < 2:
        return bytes([0x7F, 0x22, 0x13])  # NRC: incorrectMessageLengthOrInvalidFormat

    did = (request_data[0] << 8) | request_data[1]

    if did not in SUPPORTED_DIDS:
        return bytes([0x7F, 0x22, 0x31])  # NRC: requestOutOfRange

    response = bytes([0x62, (did >> 8) & 0xFF, did & 0xFF])

    if did == DID_BRAKE_PRESSURE:
        with state.lock:
            response += bytes([state.pression & 0xFF])

    return response


# --- ECUReset (0x11) ---
RESET_HARD = 0x01
RESET_SOFT = 0x03

NRC_CONDITIONS_NOT_CORRECT = 0x22

# --- ClearDiagnosticInformation (0x14) ---
GROUP_ALL_DTCS = 0xFFFFFF  # groupOfDTC "all DTCs" (ISO 14229-1)


def handle_clear_diagnostic_information(request_data, state):
    """SID 0x14. Efface les DTC correspondant au groupe demandé (payload
    déjà sans le SID, comme pour les autres handlers de ce module)."""
    if len(request_data) < 3:
        return bytes([0x7F, 0x14, 0x13])  # NRC: incorrectMessageLengthOrInvalidFormat

    group_of_dtc = (request_data[0] << 16) | (request_data[1] << 8) | request_data[2]

    with state.lock:
        if group_of_dtc == GROUP_ALL_DTCS:
            state.dtcs.clear()
        else:
            state.dtcs.pop(group_of_dtc, None)

    return bytes([0x54])


def handle_ecu_reset(request_data, state):
    """SID 0x11. Refusé si l'ECU n'est pas en session étendue (comportement
    réaliste d'un vrai calculateur : un reset ne se déclenche pas depuis la
    session par défaut). En cas de succès, remet à zéro l'état simulé de
    l'ECU (pression, état, DTC) et repasse en session par défaut."""
    if len(request_data) < 1:
        return bytes([0x7F, 0x11, 0x13])  # NRC: incorrectMessageLengthOrInvalidFormat

    reset_type = request_data[0]

    if reset_type not in (RESET_HARD, RESET_SOFT):
        return bytes([0x7F, 0x11, 0x12])  # NRC: subFunctionNotSupported

    with state.lock:
        if state.session != 0x03:
            return bytes([0x7F, 0x11, NRC_CONDITIONS_NOT_CORRECT])

        state.pression = 100
        state.etat = 0
        state.dtcs = {}
        state.session = 0x01  # retour en session par défaut après reset

    return bytes([0x51, reset_type])


def process_uds_request(request_data, state):
    if len(request_data) == 0:
        return bytes([0x7F, 0x00, 0x13])

    sid = request_data[0]
    payload = request_data[1:]

    if sid == 0x10:
        return handle_diagnostic_session_control(payload, state)
    elif sid == 0x11:
        return handle_ecu_reset(payload, state)
    elif sid == 0x14:
        return handle_clear_diagnostic_information(payload, state)
    elif sid == 0x19:
        return handle_read_dtc_information(payload, state)
    elif sid == 0x22:
        return handle_read_data_by_identifier(payload, state)
    else:
        return bytes([0x7F, sid, 0x11])  # NRC: serviceNotSupported

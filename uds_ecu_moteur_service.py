"""
Logique métier du service UDS de l'ECU Moteur (ISO 14229).

Ce module est volontairement indépendant du bus CAN / ISO-TP : il ne prend
que des octets en entrée et renvoie des octets en sortie. Cela permet de le
tester unitairement sans matériel ni bus CAN (voir test_uds_ecu_moteur.py),
conformément à l'exigence "tests unitaires sur les fonctions critiques" du
cahier des charges (section 4).

Note sur le threading : EcuState est partagé entre le thread de diffusion
périodique (qui détecte les anomalies) et le thread serveur UDS (qui répond
aux requêtes de diagnostic). Toute lecture/écriture de EcuState doit se
faire sous state.lock ; les fonctions de ce module supposent que l'appelant
détient déjà le verrou (elles ne l'acquièrent pas elles-mêmes).
"""
import threading

# --- Services UDS (SID) ---
SID_DIAGNOSTIC_SESSION_CONTROL = 0x10
SID_ECU_RESET = 0x11
SID_READ_DTC_INFORMATION = 0x19
SID_READ_DATA_BY_IDENTIFIER = 0x22

POSITIVE_RESPONSE_OFFSET = 0x40
NEGATIVE_RESPONSE_SID = 0x7F

# --- Negative Response Codes (NRC) ---
NRC_INCORRECT_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_SUB_FUNCTION_NOT_SUPPORTED = 0x12
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_REQUEST_OUT_OF_RANGE = 0x31

# Sessions supportées à ce stade du projet (ISO 14229-1)
VALID_SESSIONS = {
    0x01: "defaultSession",
    0x03: "extendedDiagnosticSession",
}

# --- ReadDTCInformation (0x19) ---
SUBFUNC_REPORT_DTC_BY_STATUS_MASK = 0x02

# Bits du statut d'un DTC (ISO 14229-1, Table D.2)
STATUS_TEST_FAILED = 0x01
STATUS_TEST_FAILED_THIS_CYCLE = 0x02
STATUS_PENDING = 0x04
STATUS_CONFIRMED = 0x08

# Codes défaut internes au projet (3 octets, format libre ; un vrai
# constructeur utiliserait sa propre table de codes ou le format OBD-II).
DTC_ENGINE_OVERHEAT = 0x010100   # Surchauffe moteur (Temperature > 110°C)
DTC_ENGINE_STALLED = 0x010200    # Moteur calé (RPM == 0 alors qu'il devrait tourner)

# --- ReadDataByIdentifier (0x22) ---
# Data Identifiers : plage F190-F1EF réservée aux DID constructeur (ISO 14229-1)
DID_ENGINE_RPM = 0xF190
DID_ENGINE_TEMPERATURE = 0xF191

SUPPORTED_DIDS = {
    DID_ENGINE_RPM: 2,          # RPM encodé sur 2 octets (big-endian)
    DID_ENGINE_TEMPERATURE: 1,  # Température sur 1 octet
}

# --- ECUReset (0x11) ---
RESET_HARD = 0x01
RESET_SOFT = 0x03

VALID_RESET_TYPES = {RESET_HARD, RESET_SOFT}

# Un reset n'est autorisé qu'en session étendue (comportement réaliste :
# un vrai calculateur refuse un reset en session par défaut, par sécurité)
SESSION_REQUISE_POUR_RESET = 0x03

# --- ClearDiagnosticInformation (0x14) ---
SID_CLEAR_DIAGNOSTIC_INFORMATION = 0x14
GROUP_ALL_DTCS = 0xFFFFFF  # groupOfDTC "all DTCs" (ISO 14229-1)


class EcuState:
    """État courant simulé de l'ECU Moteur, partagé entre les threads
    broadcast (diffusion périodique) et UDS (serveur de diagnostic)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.session = 0x01  # defaultSession au démarrage
        self.rpm = 0
        self.temperature = 0
        self.dtc = {}  # code (int) -> octet de statut (int)


def record_anomaly(state: EcuState, code: int) -> None:
    """Enregistre/actualise un DTC comme actif. À appeler avec state.lock détenu."""
    state.dtc[code] = STATUS_TEST_FAILED | STATUS_TEST_FAILED_THIS_CYCLE | STATUS_CONFIRMED


def clear_transient_status(state: EcuState, code: int) -> None:
    """Retire le bit 'testFailedThisOperationCycle' quand l'anomalie ne se
    reproduit plus dans le cycle courant, mais garde le DTC en mémoire
    (bit confirmed) tant qu'aucun ClearDiagnosticInformation (service 0x14,
    non implémenté à ce stade) n'a été reçu — comportement réaliste d'un
    vrai calculateur."""
    if code in state.dtc:
        state.dtc[code] &= ~STATUS_TEST_FAILED_THIS_CYCLE


def handle_request(payload: bytes, state: EcuState) -> bytes:
    """Traite une requête UDS brute (payload ISO-TP déjà réassemblé) et
    renvoie la réponse UDS brute à transmettre. À appeler avec state.lock
    détenu par l'appelant."""
    if len(payload) == 0:
        return bytes([NEGATIVE_RESPONSE_SID, 0x00, NRC_INCORRECT_LENGTH])

    sid = payload[0]

    if sid == SID_DIAGNOSTIC_SESSION_CONTROL:
        return _handle_session_control(payload, state)

    if sid == SID_READ_DTC_INFORMATION:
        return _handle_read_dtc_information(payload, state)

    if sid == SID_READ_DATA_BY_IDENTIFIER:
        return _handle_read_data_by_identifier(payload, state)

    if sid == SID_ECU_RESET:
        return _handle_ecu_reset(payload, state)

    if sid == SID_CLEAR_DIAGNOSTIC_INFORMATION:
        return _handle_clear_diagnostic_information(payload, state)

    # Service pas encore implémenté à cette étape du projet.
    return bytes([NEGATIVE_RESPONSE_SID, sid, NRC_SERVICE_NOT_SUPPORTED])


def _handle_session_control(payload: bytes, state: EcuState) -> bytes:
    if len(payload) < 2:
        return bytes([NEGATIVE_RESPONSE_SID, SID_DIAGNOSTIC_SESSION_CONTROL, NRC_INCORRECT_LENGTH])

    subfunction = payload[1] & 0x7F  # bit 7 = suppressPositiveResponse (non géré à ce stade)

    if subfunction not in VALID_SESSIONS:
        return bytes([NEGATIVE_RESPONSE_SID, SID_DIAGNOSTIC_SESSION_CONTROL, NRC_SUB_FUNCTION_NOT_SUPPORTED])

    state.session = subfunction

    p2_server_max = (0x00, 0x32)       # 50 ms
    p2_star_server_max = (0x01, 0xF4)  # 5000 ms (unité 10 ms)
    return bytes([
        SID_DIAGNOSTIC_SESSION_CONTROL + POSITIVE_RESPONSE_OFFSET,
        subfunction,
        *p2_server_max,
        *p2_star_server_max,
    ])


def _handle_read_dtc_information(payload: bytes, state: EcuState) -> bytes:
    if len(payload) < 3:
        return bytes([NEGATIVE_RESPONSE_SID, SID_READ_DTC_INFORMATION, NRC_INCORRECT_LENGTH])

    subfunction = payload[1]
    if subfunction != SUBFUNC_REPORT_DTC_BY_STATUS_MASK:
        return bytes([NEGATIVE_RESPONSE_SID, SID_READ_DTC_INFORMATION, NRC_SUB_FUNCTION_NOT_SUPPORTED])

    status_mask = payload[2]

    status_availability_mask = 0xFF

    response = bytearray([
        SID_READ_DTC_INFORMATION + POSITIVE_RESPONSE_OFFSET,
        subfunction,
        status_availability_mask,
    ])

    for code, status in state.dtc.items():
        if status & status_mask:
            response += bytes([(code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF, status])

    return bytes(response)


def _handle_read_data_by_identifier(payload: bytes, state: EcuState) -> bytes:
    if len(payload) < 3:
        return bytes([NEGATIVE_RESPONSE_SID, SID_READ_DATA_BY_IDENTIFIER, NRC_INCORRECT_LENGTH])

    did = (payload[1] << 8) | payload[2]

    if did not in SUPPORTED_DIDS:
        return bytes([NEGATIVE_RESPONSE_SID, SID_READ_DATA_BY_IDENTIFIER, NRC_REQUEST_OUT_OF_RANGE])

    response = bytearray([
        SID_READ_DATA_BY_IDENTIFIER + POSITIVE_RESPONSE_OFFSET,
        (did >> 8) & 0xFF,
        did & 0xFF,
    ])

    if did == DID_ENGINE_RPM:
        response += bytes([(state.rpm >> 8) & 0xFF, state.rpm & 0xFF])
    elif did == DID_ENGINE_TEMPERATURE:
        response += bytes([state.temperature & 0xFF])

    return bytes(response)


def _handle_ecu_reset(payload: bytes, state: EcuState) -> bytes:
    if len(payload) < 2:
        return bytes([NEGATIVE_RESPONSE_SID, SID_ECU_RESET, NRC_INCORRECT_LENGTH])

    reset_type = payload[1]

    if reset_type not in VALID_RESET_TYPES:
        return bytes([NEGATIVE_RESPONSE_SID, SID_ECU_RESET, NRC_SUB_FUNCTION_NOT_SUPPORTED])

    if state.session != SESSION_REQUISE_POUR_RESET:
        return bytes([NEGATIVE_RESPONSE_SID, SID_ECU_RESET, NRC_CONDITIONS_NOT_CORRECT])

    # Simule un redémarrage : remise à zéro de l'état simulé de l'ECU
    state.rpm = 0
    state.temperature = 0
    state.dtc = {}
    state.session = 0x01  # Retour en defaultSession après redémarrage

    return bytes([SID_ECU_RESET + POSITIVE_RESPONSE_OFFSET, reset_type])


def _handle_clear_diagnostic_information(payload: bytes, state: EcuState) -> bytes:
    """SID 0x14. Efface les DTC correspondant au groupe demandé. Le groupe
    0xFFFFFF ('all DTCs', ISO 14229-1) efface tous les codes défaut."""
    if len(payload) < 4:
        return bytes([NEGATIVE_RESPONSE_SID, SID_CLEAR_DIAGNOSTIC_INFORMATION, NRC_INCORRECT_LENGTH])

    group_of_dtc = (payload[1] << 16) | (payload[2] << 8) | payload[3]

    if group_of_dtc == GROUP_ALL_DTCS:
        state.dtc.clear()
    else:
        state.dtc.pop(group_of_dtc, None)

    return bytes([SID_CLEAR_DIAGNOSTIC_INFORMATION + POSITIVE_RESPONSE_OFFSET])

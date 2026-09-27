"""
Tests unitaires de la logique UDS de l'ECU Moteur.

Ne nécessite ni bus CAN ni vcan0 : on appelle directement handle_request()
avec des octets, comme le ferait le serveur UDS réel.

Lancer avec : python3 -m pytest test_uds_ecu_moteur.py -v
"""
from uds_ecu_moteur_service import (
    handle_request,
    EcuState,
    record_anomaly,
    clear_transient_status,
    DTC_ENGINE_OVERHEAT,
    DTC_ENGINE_STALLED,
    DID_ENGINE_RPM,
    DID_ENGINE_TEMPERATURE,
    RESET_HARD,
    RESET_SOFT,
    GROUP_ALL_DTCS,
)


# --- DiagnosticSessionControl (0x10) ---

def test_passage_session_etendue():
    state = EcuState()
    response = handle_request(bytes([0x10, 0x03]), state)
    assert response == bytes([0x50, 0x03, 0x00, 0x32, 0x01, 0xF4])
    assert state.session == 0x03


def test_retour_session_par_defaut():
    state = EcuState()
    response = handle_request(bytes([0x10, 0x01]), state)
    assert response == bytes([0x50, 0x01, 0x00, 0x32, 0x01, 0xF4])
    assert state.session == 0x01


def test_sous_fonction_invalide():
    state = EcuState()
    response = handle_request(bytes([0x10, 0x07]), state)
    assert response == bytes([0x7F, 0x10, 0x12])  # NRC subFunctionNotSupported


def test_requete_vide():
    state = EcuState()
    response = handle_request(bytes([]), state)
    assert response == bytes([0x7F, 0x00, 0x13])  # NRC incorrectMessageLengthOrInvalidFormat


def test_requete_session_control_trop_courte():
    state = EcuState()
    response = handle_request(bytes([0x10]), state)
    assert response == bytes([0x7F, 0x10, 0x13])


# --- ReadDTCInformation (0x19) ---

def test_lecture_dtc_aucun_defaut():
    state = EcuState()
    response = handle_request(bytes([0x19, 0x02, 0xFF]), state)
    assert response == bytes([0x59, 0x02, 0xFF])  # réponse positive, 0 DTC


def test_lecture_dtc_un_defaut_actif():
    state = EcuState()
    record_anomaly(state, DTC_ENGINE_OVERHEAT)

    response = handle_request(bytes([0x19, 0x02, 0xFF]), state)

    expected_status = 0x01 | 0x02 | 0x08  # test_failed + this_cycle + confirmed
    expected = bytes([0x59, 0x02, 0xFF, 0x01, 0x01, 0x00, expected_status])
    assert response == expected


def test_lecture_dtc_deux_defauts():
    state = EcuState()
    record_anomaly(state, DTC_ENGINE_OVERHEAT)
    record_anomaly(state, DTC_ENGINE_STALLED)

    response = handle_request(bytes([0x19, 0x02, 0xFF]), state)

    assert len(response) == 3 + 2 * 4
    assert response[0:2] == bytes([0x59, 0x02])


def test_lecture_dtc_filtre_par_status_mask():
    state = EcuState()
    record_anomaly(state, DTC_ENGINE_OVERHEAT)
    clear_transient_status(state, DTC_ENGINE_OVERHEAT)

    response = handle_request(bytes([0x19, 0x02, 0x04]), state)
    assert response == bytes([0x59, 0x02, 0xFF])


def test_lecture_dtc_sous_fonction_invalide():
    state = EcuState()
    response = handle_request(bytes([0x19, 0x99, 0xFF]), state)
    assert response == bytes([0x7F, 0x19, 0x12])


def test_lecture_dtc_requete_trop_courte():
    state = EcuState()
    response = handle_request(bytes([0x19, 0x02]), state)
    assert response == bytes([0x7F, 0x19, 0x13])


def test_service_non_supporte():
    state = EcuState()
    response = handle_request(bytes([0x2E, 0xF1, 0x90]), state)
    assert response == bytes([0x7F, 0x2E, 0x11])


# --- ReadDataByIdentifier (0x22) ---

def test_lecture_did_rpm():
    state = EcuState()
    state.rpm = 2500
    response = handle_request(bytes([0x22, 0xF1, 0x90]), state)
    assert response[0] == 0x62
    assert response[1] == 0xF1 and response[2] == 0x90
    rpm_recu = (response[3] << 8) | response[4]
    assert rpm_recu == 2500


def test_lecture_did_temperature():
    state = EcuState()
    state.temperature = 95
    response = handle_request(bytes([0x22, 0xF1, 0x91]), state)
    assert response[0] == 0x62
    assert response[3] == 95


def test_lecture_did_inconnu():
    state = EcuState()
    response = handle_request(bytes([0x22, 0xFF, 0xFF]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x31


def test_lecture_did_requete_trop_courte():
    state = EcuState()
    response = handle_request(bytes([0x22, 0xF1]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x13


# --- ECUReset (0x11) ---

def test_reset_refuse_en_session_par_defaut():
    state = EcuState()
    response = handle_request(bytes([0x11, RESET_HARD]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x22  # NRC conditionsNotCorrect


def test_reset_accepte_en_session_etendue():
    state = EcuState()
    handle_request(bytes([0x10, 0x03]), state)
    state.rpm = 3000
    state.temperature = 95
    record_anomaly(state, DTC_ENGINE_OVERHEAT)

    response = handle_request(bytes([0x11, RESET_HARD]), state)

    assert response == bytes([0x51, RESET_HARD])
    assert state.rpm == 0
    assert state.temperature == 0
    assert state.dtc == {}
    assert state.session == 0x01


def test_reset_type_invalide():
    state = EcuState()
    handle_request(bytes([0x10, 0x03]), state)
    response = handle_request(bytes([0x11, 0xFF]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x12


def test_reset_requete_vide():
    state = EcuState()
    handle_request(bytes([0x10, 0x03]), state)
    response = handle_request(bytes([0x11]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x13


# --- ClearDiagnosticInformation (0x14) ---

def test_clear_dtc_tous():
    state = EcuState()
    record_anomaly(state, DTC_ENGINE_OVERHEAT)
    record_anomaly(state, DTC_ENGINE_STALLED)

    group = GROUP_ALL_DTCS
    response = handle_request(bytes([0x14, (group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF]), state)

    assert response == bytes([0x54])
    assert state.dtc == {}


def test_clear_dtc_code_specifique():
    state = EcuState()
    record_anomaly(state, DTC_ENGINE_OVERHEAT)
    record_anomaly(state, DTC_ENGINE_STALLED)

    group = DTC_ENGINE_OVERHEAT
    response = handle_request(bytes([0x14, (group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF]), state)

    assert response == bytes([0x54])
    assert DTC_ENGINE_OVERHEAT not in state.dtc
    assert DTC_ENGINE_STALLED in state.dtc


def test_clear_dtc_code_inconnu_ne_plante_pas():
    state = EcuState()
    record_anomaly(state, DTC_ENGINE_OVERHEAT)

    response = handle_request(bytes([0x14, 0x99, 0x99, 0x99]), state)

    assert response == bytes([0x54])
    assert DTC_ENGINE_OVERHEAT in state.dtc  # inchangé


def test_clear_dtc_requete_trop_courte():
    state = EcuState()
    response = handle_request(bytes([0x14, 0xFF, 0xFF]), state)
    assert response[0] == 0x7F
    assert response[1] == 0x14
    assert response[2] == 0x13

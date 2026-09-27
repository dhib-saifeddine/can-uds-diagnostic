import pytest
from uds_ecu_freinage_service import (
    EcuFreinageState,
    handle_diagnostic_session_control,
    handle_read_dtc_information,
    handle_read_data_by_identifier,
    handle_ecu_reset,
    process_uds_request,
    DTC_BRAKE_PRESSURE_LOW,
    DTC_BRAKE_WEAR,
    DID_BRAKE_PRESSURE,
    RESET_HARD,
    RESET_SOFT,
    handle_clear_diagnostic_information,
    GROUP_ALL_DTCS,
)


@pytest.fixture
def state():
    return EcuFreinageState()


# --- Tests DiagnosticSessionControl (SID 0x10) ---

def test_passage_session_etendue(state):
    response = handle_diagnostic_session_control(bytes([0x03]), state)
    assert response[0] == 0x50
    assert response[1] == 0x03
    assert state.session == 0x03


def test_retour_session_par_defaut(state):
    handle_diagnostic_session_control(bytes([0x03]), state)
    response = handle_diagnostic_session_control(bytes([0x01]), state)
    assert response[0] == 0x50
    assert state.session == 0x01


def test_sous_fonction_invalide(state):
    response = handle_diagnostic_session_control(bytes([0xFF]), state)
    assert response[0] == 0x7F
    assert response[1] == 0x10
    assert response[2] == 0x12  # subFunctionNotSupported


def test_requete_vide(state):
    response = handle_diagnostic_session_control(bytes([]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x13  # incorrectMessageLengthOrInvalidFormat


# --- Tests ReadDTCInformation (SID 0x19) ---

def test_lecture_dtc_aucun_defaut(state):
    response = handle_read_dtc_information(bytes([0x02, 0xFF]), state)
    assert response[0] == 0x59
    assert response[1] == 0x02
    assert len(response) == 3  # Aucun DTC ajouté


def test_lecture_dtc_pression_basse(state):
    state.set_anomalie(DTC_BRAKE_PRESSURE_LOW)
    response = handle_read_dtc_information(bytes([0x02, 0xFF]), state)
    assert len(response) == 7  # header (3) + 1 DTC (4 octets)
    code_recu = int.from_bytes(response[3:6], 'big')
    assert code_recu == DTC_BRAKE_PRESSURE_LOW


def test_lecture_dtc_deux_defauts(state):
    state.set_anomalie(DTC_BRAKE_PRESSURE_LOW)
    state.set_anomalie(DTC_BRAKE_WEAR)
    response = handle_read_dtc_information(bytes([0x02, 0xFF]), state)
    assert len(response) == 11  # header (3) + 2 DTC (4 octets chacun)


def test_lecture_dtc_sous_fonction_invalide(state):
    response = handle_read_dtc_information(bytes([0x99, 0xFF]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x12


def test_lecture_dtc_requete_trop_courte(state):
    response = handle_read_dtc_information(bytes([0x02]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x13


# --- Tests ReadDataByIdentifier (SID 0x22) ---

def test_lecture_did_pression(state):
    state.pression = 75
    response = handle_read_data_by_identifier(bytes([0xF1, 0xA0]), state)
    assert response[0] == 0x62
    assert response[1] == 0xF1 and response[2] == 0xA0
    assert response[3] == 75


def test_lecture_did_inconnu(state):
    response = handle_read_data_by_identifier(bytes([0xFF, 0xFF]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x31  # NRC requestOutOfRange


def test_lecture_did_requete_trop_courte(state):
    response = handle_read_data_by_identifier(bytes([0xF1]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x13  # NRC incorrectMessageLengthOrInvalidFormat


def test_dispatch_read_data_by_identifier(state):
    state.pression = 42
    response = process_uds_request(bytes([0x22, 0xF1, 0xA0]), state)
    assert response[0] == 0x62
    assert response[3] == 42


# --- Tests ECUReset (SID 0x11) ---

def test_reset_refuse_hors_session_etendue(state):
    # state.session == 0x01 (defaultSession) par défaut
    response = handle_ecu_reset(bytes([RESET_HARD]), state)
    assert response[0] == 0x7F
    assert response[1] == 0x11
    assert response[2] == 0x22  # NRC conditionsNotCorrect


def test_reset_accepte_en_session_etendue_et_remet_a_zero(state):
    handle_diagnostic_session_control(bytes([0x03]), state)  # passe en session étendue
    state.pression = 12
    state.etat = 1
    state.set_anomalie(DTC_BRAKE_PRESSURE_LOW)

    response = handle_ecu_reset(bytes([RESET_HARD]), state)

    assert response == bytes([0x51, RESET_HARD])
    assert state.pression == 100
    assert state.etat == 0
    assert state.dtcs == {}
    assert state.session == 0x01  # retour en session par défaut après reset


def test_reset_type_invalide(state):
    handle_diagnostic_session_control(bytes([0x03]), state)
    response = handle_ecu_reset(bytes([0x99]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x12  # NRC subFunctionNotSupported


def test_reset_requete_vide(state):
    response = handle_ecu_reset(bytes([]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x13  # NRC incorrectMessageLengthOrInvalidFormat


def test_dispatch_ecu_reset(state):
    handle_diagnostic_session_control(bytes([0x03]), state)
    response = process_uds_request(bytes([0x10, 0x03]), state)  # re-confirme la session
    response = process_uds_request(bytes([0x11, RESET_SOFT]), state)
    assert response == bytes([0x51, RESET_SOFT])


# --- Tests ClearDiagnosticInformation (SID 0x14) ---

def test_clear_dtc_tous(state):
    state.set_anomalie(DTC_BRAKE_PRESSURE_LOW)
    state.set_anomalie(DTC_BRAKE_WEAR)

    group = GROUP_ALL_DTCS
    response = handle_clear_diagnostic_information(
        bytes([(group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF]), state
    )

    assert response == bytes([0x54])
    assert state.dtcs == {}


def test_clear_dtc_code_specifique(state):
    state.set_anomalie(DTC_BRAKE_PRESSURE_LOW)
    state.set_anomalie(DTC_BRAKE_WEAR)

    group = DTC_BRAKE_PRESSURE_LOW
    response = handle_clear_diagnostic_information(
        bytes([(group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF]), state
    )

    assert response == bytes([0x54])
    assert DTC_BRAKE_PRESSURE_LOW not in state.dtcs
    assert DTC_BRAKE_WEAR in state.dtcs


def test_clear_dtc_requete_trop_courte(state):
    response = handle_clear_diagnostic_information(bytes([0xFF, 0xFF]), state)
    assert response[0] == 0x7F
    assert response[1] == 0x14
    assert response[2] == 0x13


def test_dispatch_clear_dtc(state):
    state.set_anomalie(DTC_BRAKE_WEAR)
    response = process_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), state)
    assert response == bytes([0x54])
    assert state.dtcs == {}


# --- Tests process_uds_request (dispatcher global) ---

def test_service_non_supporte(state):
    response = process_uds_request(bytes([0xAA, 0x00]), state)
    assert response[0] == 0x7F
    assert response[2] == 0x11  # serviceNotSupported


def test_dispatch_session(state):
    response = process_uds_request(bytes([0x10, 0x03]), state)
    assert response[0] == 0x50

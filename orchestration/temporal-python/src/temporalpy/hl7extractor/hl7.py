import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional, TextIO

from temporalio.exceptions import ApplicationError

import hl7

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatientIdentifier:
    id_number: Optional[str]
    assigning_authority: Optional[str]
    identifier_type_code: Optional[str]


@dataclass(frozen=True)
class MessageData:
    source_file: Optional[str]
    message_dt: Optional[str]
    sending_facility: Optional[str]
    message_control_id: Optional[str]
    version_id: Optional[str]
    patient_id_json: Optional[str]
    birth_date: Optional[str]
    sex: Optional[str]
    race: Optional[str]
    zip_or_postal_code: Optional[str]
    country: Optional[str]
    ethnic_group: Optional[str]
    orc_2_placer_order_number: Optional[str]
    obr_2_placer_order_number: Optional[str]
    orc_3_filler_order_number: Optional[str]
    obr_3_filler_order_number: Optional[str]
    service_identifier: Optional[str]
    service_name: Optional[str]
    service_coding_system: Optional[str]
    requested_dt: Optional[str]
    observation_dt: Optional[str]
    observation_end_dt: Optional[str]
    results_report_status_change_dt: Optional[str]
    diagnostic_service_id: Optional[str]
    obr_25_result_status: Optional[str]
    report_text: Optional[str]
    obx_11_observation_result_status: Optional[str]
    diagnosis_code: Optional[str]
    diagnosis_code_text: Optional[str]
    diagnosis_code_coding_system: Optional[str]
    study_instance_uid: Optional[str]


def _read_hl7_message(filename: str) -> hl7.Message:
    """Read HL7 message from file."""
    log.info("Reading HL7 message from local file %s", filename)
    with open(filename, "r", encoding="latin-1", newline="\r") as f:
        return parse_hl7_message(f)


def parse_hl7_message(data: TextIO) -> hl7.Message:
    return hl7.parse(data.read())


def read_hl7_message(path: str) -> MessageData:
    """Read HL7 message from file."""
    try:
        message = _read_hl7_message(path)
        log.debug("Successfully read HL7 message from %s", path)
        return extract_data(message, path)
    except Exception as e:
        raise ApplicationError(f"Error extracting {path}") from e


def extract_patient_identifiers(
    message: hl7.Message,
) -> Optional[list[PatientIdentifier]]:
    r"""Extract Patient Identifiers from PID-3 in HL7 message.

    PID-3 is a list (delimited by ~) of Patient Identifiers of the form
        <ID Number (ST)> ^^^ <Assigning Authority (HD)> ^ <Identifier Type Code (ID)>

    >>> message_str = r'''
    ... MSH|^~\&|EPIC|ABC|PACS|ABC|20210101120000||ORU^R01|123456|P|2.7
    ... PID|1||123456789^^^EPIC^MRN~0000000001^^^ABC^MR|
    ... '''.replace("\n", "\r")
    >>> message = hl7.parse(message_str)
    >>> extract_patient_identifiers(message)
    [PatientIdentifier(id_number='123456789', assigning_authority='EPIC', identifier_type_code='MRN'), PatientIdentifier(id_number='0000000001', assigning_authority='ABC', identifier_type_code='MR')]
    """
    log.debug(f"Extracting Patient IDs from PID-3")

    pid_seg = message.segment("PID")
    if not pid_seg:
        log.debug("PID segment not found in message")
        return None
    if len(pid_seg) < 3:
        log.debug("PID-3 not found in PID segment")
        return None

    pid3s: hl7.Field = pid_seg(3)
    if not pid3s:
        log.debug("PID-3 not found in PID segment")
        return None

    return [
        PatientIdentifier(
            id_number=pid3(1)(1),
            assigning_authority=pid3(4)(1),
            identifier_type_code=pid3(5)(1),
        )
        for pid3 in pid3s
    ]


def extract_report_status_from_obx11(message: hl7.Message) -> Optional[str]:
    """Extract Report Status from OBX-11 in HL7 message."""
    log.debug("Extracting Report Status from OBX-11")
    try:
        obx_segments = message.segments("OBX")
    except LookupError:
        obx_segments = None
    if not obx_segments:
        log.debug("OBX segment not found in message")
        return None

    report_statuses = set()
    for obx in obx_segments:
        if len(obx) < 11:
            log.debug("Skipping OBX segment with no OBX-11")
            continue
        status = str(obx(11))
        if not status:
            log.debug("Skipping OBX segment with empty OBX-11")
            continue
        report_statuses.add(status)

    if not report_statuses:
        log.debug("No Report Status OBX-11 found in OBX segments")
        return None
    if len(report_statuses) > 1:
        log.warning(
            f"Multiple Report Statuses found in OBX-11: {report_statuses}. Returning first."
        )
    return report_statuses.pop()


def extract_field(
    message: hl7.Message,
    segment: str,
    field: int,
    repeat: int = 1,
    component: int = 1,
    subcomponent: int = 1,
) -> Optional[str]:
    """Extract a simple field from an HL7 message."""
    log.debug(f"Extracting {segment}-{field} ({repeat}:{component}:{subcomponent})")
    try:
        return (
            message.extract_field(segment, 1, field, repeat, component, subcomponent)
            or None
        )
    except LookupError:
        log.warning(
            f"{segment}-{field} ({repeat}:{component}:{subcomponent}) not found in message"
        )
        return None


def extract_and_join_reports(message: hl7.Message) -> Optional[str]:
    r"""Extract all OBX-5 values in HL7 message and join with \n.

    >>> message_str = r'''
    ... MSH|^~\&|EPIC|ABC|PACS|ABC|20210101120000||ORU^R01|123456|P|2.7
    ... OBX|1|ST|A|1|This is the report text.|||Status|
    ... OBX|2|FT|B|2|Also include this text|||Status|
    ... OBX|3|FT|B|2|in the report|||Status|
    ... OBX|4|ST|C|3|This section has|||Status|
    ... OBX|5|ST|C|3||||Status|
    ... OBX|6|ST|C|3|an empty line|||Status|
    ... OBX|7|TX|D|4|This has lots of text~in one field~on multiple lines~~Neat!|||Status|
    ... OBX|8|RP|E|5|123^Skip^Me|||Status|
    ... '''.replace("\n", "\r")
    >>> message = hl7.parse(message_str)
    >>> extract_and_join_reports(message)
    'This is the report text.\nAlso include this text\nin the report\nThis section has\n\nan empty line\nThis has lots of text\nin one field\non multiple lines\n\nNeat!'
    """
    log.debug("Extracting all Report Text from OBX-5")
    try:
        obx_segments = message.segments("OBX")
    except LookupError:
        obx_segments = None
    if not obx_segments:
        log.debug("OBX segment not found in message")
        return None

    report_lines = []
    for obx in obx_segments:
        if obx(2) == ["TX"]:  # Text data type, multiple lines
            report_lines.extend(map(str, obx(5)))
        # ST: String, single line; FT: Formatted text, single line
        elif obx(2) in (["ST"], ["FT"]):
            report_lines.append(str(obx(5)))
        else:
            log.debug(
                f"Skipping OBX segment {obx(1)} with unsupported OBX-2 data type {obx(2)}"
            )
            continue

    return "\n".join(report_lines)


def extract_data(message: hl7.Message, path: Optional[str] = None) -> MessageData:
    """Extract data from HL7 message."""
    return MessageData(
        source_file=path,
        message_dt=extract_field(message, "MSH", 7),
        sending_facility=extract_field(message, "MSH", 4),
        message_control_id=extract_field(message, "MSH", 10),
        version_id=extract_field(message, "MSH", 12),
        patient_id_json=json.dumps(
            list(map(asdict, extract_patient_identifiers(message) or []))
        ),
        birth_date=extract_field(message, "PID", 7),
        sex=extract_field(message, "PID", 8),
        race=extract_field(message, "PID", 10),
        zip_or_postal_code=extract_field(message, "PID", 11, component=5),
        country=extract_field(message, "PID", 11, component=6),
        ethnic_group=extract_field(message, "PID", 22),
        orc_2_placer_order_number=extract_field(message, "ORC", 2),
        obr_2_placer_order_number=extract_field(message, "OBR", 2),
        orc_3_filler_order_number=extract_field(message, "ORC", 3),
        obr_3_filler_order_number=extract_field(message, "OBR", 3),
        service_identifier=extract_field(message, "OBR", 4, component=1),
        service_name=extract_field(message, "OBR", 4, component=2),
        service_coding_system=extract_field(message, "OBR", 4, component=3),
        requested_dt=extract_field(message, "OBR", 6),
        observation_dt=extract_field(message, "OBR", 7),
        observation_end_dt=extract_field(message, "OBR", 8),
        results_report_status_change_dt=extract_field(message, "OBR", 22),
        diagnostic_service_id=extract_field(message, "OBR", 24),
        obr_25_result_status=extract_field(message, "OBR", 25),
        report_text=extract_and_join_reports(message),
        obx_11_observation_result_status=extract_report_status_from_obx11(message),
        diagnosis_code=extract_field(message, "DG1", 3, component=1),
        diagnosis_code_text=extract_field(message, "DG1", 3, component=2),
        diagnosis_code_coding_system=extract_field(message, "DG1", 3, component=3),
        study_instance_uid=extract_field(message, "ZDS", 1),
    )


def extract_filename(path: str) -> str:
    """Extract filename from path."""
    return None if path is None else os.path.basename(path)

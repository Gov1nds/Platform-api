"""ITAR / EAR export-control flagging (Blueprint §31.5)."""
ITAR_COMMODITY_GROUPS = {"defense_electronics", "munitions_grade_metals", "restricted_semiconductors"}
EAR_COMMODITY_GROUPS = {"high_performance_computing", "cryptography_hw", "dual_use_sensors"}

def flag_export_control(part_id, commodity_group, delivery_country=None, vendor_country=None):
    flags = []
    if commodity_group in ITAR_COMMODITY_GROUPS:
        flags.append({"type": "ITAR", "severity": "high",
                      "mitigation": "Requires DDTC export license; US persons-only handling"})
    if commodity_group in EAR_COMMODITY_GROUPS:
        flags.append({"type": "EAR", "severity": "high",
                      "mitigation": "Verify BIS license requirement for destination country"})
    return flags

from src.policy.redaction import redact_record, redact_value


def test_redacts_value_whose_field_name_is_sensitive():
    assert redact_value("hunter2", field_name="password") == "[REDACTED]"


def test_does_not_redact_value_whose_field_name_is_not_sensitive():
    assert redact_value("12345", field_name="memberId") == "12345"


def test_redacts_ssn_shaped_value_regardless_of_field_name():
    assert redact_value("123-45-6789", field_name="notes") == "[REDACTED]"


def test_redacts_card_number_shaped_value_regardless_of_field_name():
    assert redact_value("4111111111111111", field_name="notes") == "[REDACTED]"


def test_redact_record_redacts_only_flagged_or_sensitive_looking_fields():
    out = redact_record({"memberId": "12345", "password": "hunter2", "note": "ok"})
    assert out["memberId"] == "12345"
    assert out["password"] == "[REDACTED]"
    assert out["note"] == "ok"

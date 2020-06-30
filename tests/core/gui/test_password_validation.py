# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.core.gui.lang import switch_language
from parsec.core.gui.password_validation import get_password_strength, get_password_strength_text


@pytest.mark.gui
def test_password_validation():
    assert get_password_strength("passwor") == 0
    assert get_password_strength("password") == 1
    assert get_password_strength("password-1") == 2
    assert get_password_strength("password-123") == 3
    assert get_password_strength("password-123_test") == 4
    assert get_password_strength("password-123_test-abc") == 5


@pytest.mark.gui
def test_password_text(core_config):
    switch_language(core_config, "en")
    assert get_password_strength_text(0) == "TOO SHORT"
    assert get_password_strength_text(1) == "VERY WEAK"
    assert get_password_strength_text(2) == "WEAK"
    assert get_password_strength_text(3) == "AVERAGE"
    assert get_password_strength_text(4) == "GOOD"
    assert get_password_strength_text(5) == "EXCELLENT"

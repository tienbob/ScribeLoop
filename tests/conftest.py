"""Optional native integration dependencies; core/mocked tests stay portable."""
import os

import pytest


@pytest.fixture()
def require_sqlcipher():
    if os.name != 'posix':
        pytest.skip('Encrypted-store lifecycle requires POSIX')
    pytest.importorskip('sqlcipher3.dbapi2',reason='Native SQLCipher integration; required in Linux CI')

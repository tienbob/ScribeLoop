"""Real Google library with locally signed JWTs and synthetic cert transport."""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from scribeloop_core.accounts.contracts import GOOGLE_CLIENT_ID
from scribeloop_core.accounts.server import GoogleVerifier

pytest.importorskip('google.auth')


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')


@pytest.mark.parametrize('fault',[None,'audience','issuer','expired','signature'])
def test_real_google_verifier_rejects_invalid_identity(fault):
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    now = int(time.time())
    claims = {'iss':'https://accounts.google.com','aud':GOOGLE_CLIENT_ID,'iat':now,
              'exp':now+300,'sub':'verified-subject','nonce':'test-nonce','email_verified':True}
    if fault=='audience': claims['aud'] = 'another-client'
    if fault=='issuer': claims['iss'] = 'https://attacker.example'
    if fault=='expired': claims['exp'] = now-120
    header = encode(json.dumps({'alg':'RS256','kid':'test-key'}).encode())
    payload = encode(json.dumps(claims).encode())
    message = header+b'.'+payload
    signature = key.sign(message,padding.PKCS1v15(),hashes.SHA256())
    if fault=='signature': signature = bytes([signature[0]^1])+signature[1:]
    token = (message+b'.'+encode(signature)).decode()
    class Response:
        status = 200
        data = json.dumps({'test-key':public}).encode()
    verifier = GoogleVerifier(GOOGLE_CLIENT_ID)
    verifier.request = lambda *args,**kwargs:Response()
    if fault:
        with pytest.raises(ValueError): verifier(token)
    else:
        assert verifier(token)['sub']=='verified-subject'

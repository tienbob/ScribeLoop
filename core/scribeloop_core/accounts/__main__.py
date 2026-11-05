"""Run behind a TLS reverse proxy; defaults to loopback, never the desktop port."""
import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .contracts import AccountConfig
from .server import create_account_app
from .store import AccountStore


def main():
    parser = argparse.ArgumentParser(description='ScribeLoop account service')
    parser.add_argument('--generate-key',type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--database',type=Path)
    parser.add_argument('--signing-key',type=Path)
    parser.add_argument('--port',type=int,default=8091)
    parser.add_argument('--trust-loopback-proxy',action='store_true',
                        help='Trust forwarded headers only from a same-host IPv4 proxy')
    args = parser.parse_args()
    if args.generate_key:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        fd = os.open(args.generate_key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as output:
            output.write(raw)
        print('Public entitlement verification key:')
        print(base64.b64encode(key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode())
        return
    if not args.config or not args.database or not args.signing_key:
        parser.error('Provide --config, --database and --signing-key')
    if not 1024<=args.port<=65535:
        parser.error('Choose a port from 1024 through 65535')
    # New database and any SQLite sidecars inherit owner-only permissions.
    os.umask(0o077)
    if args.signing_key.stat().st_mode & 0o077:
        parser.error('Signing key permissions must be owner-only')
    config = AccountConfig.model_validate_json(args.config.read_text())
    key = Ed25519PrivateKey.from_private_bytes(args.signing_key.read_bytes())
    store = AccountStore(args.database)
    try:
        app = create_account_app(config,store,key)
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False,
                    proxy_headers=args.trust_loopback_proxy,forwarded_allow_ips='127.0.0.1',workers=1)
    finally:
        store.conn.close()


if __name__=='__main__':
    main()

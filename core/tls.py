"""TLS/SSL self-signed certificate generator for secure HTTPS and WSS connections."""

import datetime
import ipaddress
import os
import socket
from pathlib import Path
from typing import List, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def get_local_ips() -> List[str]:
    """Retrieve all local network IP addresses for Subject Alternative Names."""
    ips = ["127.0.0.1", "::1"]
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    try:
        # Connect to a dummy external IP to determine primary LAN interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        if primary_ip not in ips:
            ips.append(primary_ip)
        s.close()
    except Exception:
        pass

    return ips


def generate_self_signed_cert(
    cert_dir: str = "certs",
    cert_name: str = "cert.pem",
    key_name: str = "key.pem",
    validity_days: int = 365,
) -> Tuple[str, str]:
    """
    Generate or load a self-signed TLS certificate and private key.
    Returns (cert_path, key_path).
    """
    cert_path = Path(cert_dir) / cert_name
    key_path = Path(cert_dir) / key_name

    cert_path.parent.mkdir(parents=True, exist_ok=True)

    # Return existing files if valid and present
    if cert_path.exists() and key_path.exists() and cert_path.stat().st_size > 0 and key_path.stat().st_size > 0:
        return str(cert_path.resolve()), str(key_path.resolve())

    # Generate RSA private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Prepare Subject / Issuer
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Remote Desktop"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Self-Hosted Remote Desktop"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    # Add SANs for localhost and local IPs
    san_list: List[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName("*.local"),
    ]

    for ip_str in get_local_ips():
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            san_list.append(x509.IPAddress(ip_obj))
        except ValueError:
            pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
    )

    cert = cert_builder.sign(private_key, hashes.SHA256())

    # Write private key
    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Write certificate
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return str(cert_path.resolve()), str(key_path.resolve())

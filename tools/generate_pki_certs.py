import os
import argparse
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

def generate_ca(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    ca_key_path = os.path.join(out_dir, "ca.key")
    ca_crt_path = os.path.join(out_dir, "ca.crt")

    if os.path.exists(ca_key_path) and os.path.exists(ca_crt_path):
        print(f"Using existing Root CA at: {ca_crt_path}")
        with open(ca_key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None)
        with open(ca_crt_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read())
        return ca_key, ca_cert

    print("Generating Root Certificate Authority (CA)...")
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "UABAMS Railways"),
        x509.NameAttribute(NameOID.COMMON_NAME, "UABAMS Root CA"),
    ])

    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    with open(ca_key_path, "wb") as f:
        f.write(ca_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open(ca_crt_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    print(f"Created Root CA certificate: {ca_crt_path}")
    return ca_key, ca_cert

def generate_device_cert(gateway_id: str, ca_key, ca_cert, out_dir: str):
    dev_key_path = os.path.join(out_dir, f"{gateway_id}.key")
    dev_crt_path = os.path.join(out_dir, f"{gateway_id}.crt")

    device_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "UABAMS Gateway Device"),
        x509.NameAttribute(NameOID.COMMON_NAME, gateway_id),
    ])

    device_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(device_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    with open(dev_key_path, "wb") as f:
        f.write(device_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open(dev_crt_path, "wb") as f:
        f.write(device_cert.public_bytes(serialization.Encoding.PEM))

    print(f"Generated Device Cert for [{gateway_id}]: {dev_crt_path}")

def main():
    parser = argparse.ArgumentParser(description="Generate Root CA and Device X.509 Certificates")
    parser.add_argument("--gateways", type=str, default="GW_UABAMS_BOGIE_01,GW_UABAMS_BOGIE_02", help="Comma separated gateway IDs")
    parser.add_argument("--out-dir", type=str, default="certs", help="Output directory for certificates")
    args = parser.parse_args()

    ca_key, ca_cert = generate_ca(args.out_dir)
    
    gateway_list = [g.strip() for g in args.gateways.split(",") if g.strip()]
    for gid in gateway_list:
        generate_device_cert(gid, ca_key, ca_cert, args.out_dir)

    print(f"\nBatch Certificate Generation Complete for {len(gateway_list)} gateways in '{args.out_dir}'!")

if __name__ == "__main__":
    main()

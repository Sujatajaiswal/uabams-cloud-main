FROM alpine:3.18

RUN apk add --no-cache openssh rsync bash

# Generate host keys
RUN ssh-keygen -A

# Create user
RUN adduser -D -s /bin/bash -u 1001 uabams_upload && \
    echo "uabams_upload:*" | chpasswd -e

# Configure SSHD
RUN sed -i 's/^#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -i 's/^#PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config

# Create incoming symlink for absolute paths
RUN ln -s /home/uabams_upload/incoming /incoming

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

#!/bin/bash

# Ensure the directories exist
mkdir -p /home/uabams_upload/.ssh
mkdir -p /home/uabams_upload/incoming

# Set exact permissions required by OpenSSH StrictModes
chown -R uabams_upload:uabams_upload /home/uabams_upload/.ssh
chmod 700 /home/uabams_upload/.ssh

if [ -f /home/uabams_upload/.ssh/authorized_keys ]; then
    chmod 600 /home/uabams_upload/.ssh/authorized_keys
fi

chown -R uabams_upload:uabams_upload /home/uabams_upload/incoming

# Start SSH daemon
exec /usr/sbin/sshd -D -e

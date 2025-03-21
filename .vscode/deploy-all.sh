#!/bin/bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/me3
for server in "$@"; do
    rsync -avz -e "ssh -o StrictHostKeyChecking=no -p ${server##*:}" ./ ${server%%:*}:/root/glowflye/ublox-SARA-R5
done

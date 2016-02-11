#!/bin/sh

set -e
if [ -f /volumes/$VOLUME_ID/.mounted ]; then
    echo "Mounted!"
    while true; do
        sleep 2
    done
else
    echo "Mount not found"
    exit 1
fi
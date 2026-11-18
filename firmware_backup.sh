#!/bin/bash

SIZE_16MB=0x1000000
SAVE_TO_FILE=$1

if [ "$1" == "" ]; then
    echo "Usage: $0 <backup_filename>"
    exit 1
fi

chmod +x mvf/mvf_*
SAVE_TO_FILE=$(realpath -m "$SAVE_TO_FILE")
cd mvf && ./mvf_64 -rfb "$SAVE_TO_FILE" -addr 0x00000000 -sz $SIZE_16MB && cd ..
#!/bin/sh

hl7log_fullpath=${1:?Pass path to .log file as input}

# Check if file exists
if [ ! -f $hl7log_fullpath ]; then
    echo "File not found: $hl7log_fullpath" >&2
    exit 1
fi

hl7log=$(basename $hl7log_fullpath)
prefix=${hl7log%.*}

# Split big NAME.log file on lines with only carriage return
# Produces many NAME.ddddd files
csplit --digits 5 --elide-empty-files --suppress-matched --silent --prefix $prefix. $hl7log_fullpath /^$'\r'/ '{*}'

# Output all split NAME.ddddd files
ls $prefix.* | grep -v $hl7log

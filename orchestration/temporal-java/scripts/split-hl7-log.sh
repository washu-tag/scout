#!/bin/sh

hl7log_fullpath=${1:?Pass path to .log file as input}
hl7log=$(basename $hl7log_fullpath)
prefix=${hl7log%.*}

# Split big NAME.log file on lines with only carriage return
# Produces many NAME.ddddd files
csplit --digits 5 --elide-empty-files --suppress-matched --silent --prefix $prefix. $hl7log_fullpath /^$'\r'/ '{*}'

# Output all split NAME.ddddd files
ls $prefix.* | grep -v $hl7log

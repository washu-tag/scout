#!/bin/sh

f=${1:?Provide absolute path to split log file as first argument}

# Check if file exists
if [ ! -f $f ]; then
    echo "File not found: $f" >&2
    exit 1
fi

# Read header from file to get 18-digit timestamp
# Format: %Y%m%d%H%M%S%f in python strftime notation
timestamp=$(head -c 24 $f | tr -C -d \[:digit:\])

# Make a directory to store the file, of the format year/month/day/hour
directory=${timestamp:0:4}/${timestamp:4:2}/${timestamp:6:2}/${timestamp:8:2}
mkdir -p $directory

dest="$directory/$timestamp.hl7"

# Format the file as HL7
#   1. Remove two header lines
#   2. Remove one footer line
#   3. Remove <R> at the end of lines
#   4. Replace \n with \r (yes, this is a requirement for HL7)
# Write new file to timestamped directory
# TODO sed -e to use sed once
tail -n +3 $f | sed \$d | sed 's/<R>$//' | tr $'\n' $'\r' > $dest

# output new file relative path
echo $dest

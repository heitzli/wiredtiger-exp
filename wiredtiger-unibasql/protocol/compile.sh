
#!/usr/bin/env bash

set -euo pipefail

# Run from the directory containing this script.
cd "$(dirname "$0")"

# Remove previously generated files.
rm -f ./*.avpr ./*.avsc

# Compile each Avro IDL file.
for idl in ./*.avdl; do
    [[ -f "$idl" ]] || continue

    base="$(basename "$idl" .avdl)"

    # Choose the output format for each file.
    case "$base" in
        algebranode|errors)
            format="avsc"
            ;;
        broker|client|message|queryprocessor|storagemanager)
            format="avpr"
            ;;
        *)
            format="avpr"
            ;;
    esac

    output="${base}.${format}"

    echo "Compiling ${idl} -> ${output}"
    avdl idl "$idl" "$output"
done

echo "All Avro IDL files compiled successfully."

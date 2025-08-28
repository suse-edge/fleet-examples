#!/bin/bash

usage () {
    echo "USAGE: $0 --archive-dir /foo/bar --fleet-path /foo/bar/eib-charts-upgrader"
    echo "  [-a|--archive-dir] directory containing Helm *.tgz archives."
    echo "  [-f|--fleet-path] path to the root of the 'eib-charts-upgrader' fleet."
    echo "  [-h|--help] Usage message"
}

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -a|--archive-dir)
        archive_dir="$2"
        shift # past argument
        shift # past value
        ;;
        -f|--fleet-path)
        fleet_path="$2"
        shift # past argument
        shift # past value
        ;;
        -h|--help)
        usage
        exit 0
        ;;
        *)
        usage
        exit 1
        ;;
    esac
done

if ! command -v "kubectl" &> /dev/null; then
    echo "Script requires 'kubectl' to generate the needed secrets."
    echo "For 'kubectl' installation instructions, see https://kubernetes.io/docs/tasks/tools/#kubectl"
    exit 1
fi

if [[ -z "$archive_dir" || -z "$fleet_path" ]]; then
    echo "Error: Missing required arguments."
    usage
    exit 1
fi

for dir in "$archive_dir" "$fleet_path"; do
    if [[ ! -d "$dir" ]]; then
        echo "Error: Directory '$dir' does not exist."
        usage
        exit 1
    fi
done

secrets_path=$fleet_path/base/secrets
if [[ ! -d "$secrets_path" ]]; then
    echo "Error: Directory '$secrets_path' does not exist. '--fleet-path' is not pointing to a valid 'eib-charts-upgrader' fleet."
    exit 1
fi

# Validate essential fleet files are there
secrets_kustomize=$secrets_path/kustomization.yaml
eib_script_secret_path="$secrets_path/eib-charts-upgrader-script.yaml"
job_patch="$fleet_path/base/patches/job-patch.yaml"
for file in "$secrets_kustomize" "$eib_script_secret_path" "$job_patch"; do
    if [[ ! -f "$file" ]]; then
        error_exit "File '$file' does not exist. '--fleet-path' may not be valid."
    fi
done

# Use correct base64 flags for OS type
if [[ "$OSTYPE" == "linux"* ]]; then
    base64_option="-w 0"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    base64_option="-b 0 -i"
else
    echo "Unsupported OS"
    exit 1
fi

# Cleanup from previous run
eib_script_secret_name=$(basename "$eib_script_secret_path")
secret_kustomization_name=$(basename "$secrets_kustomize")
sed '/sources:/q' "$job_patch" > "$job_patch.tmp" && mv "$job_patch.tmp" "$job_patch"
sed "/$eib_script_secret_name/q" "$secrets_kustomize" > "$secrets_kustomize.tmp" && mv "$secrets_kustomize.tmp" "$secrets_kustomize"
find $secrets_path -type f ! -name $eib_script_secret_name ! -name $secret_kustomization_name -exec rm -f {} \;

# Secret generation
chart_suffix="-chart"
for archive in "$archive_dir"/*.tgz; do
    # Account for empty dir, where the for loop will try to iterate over
    # the pattern
    [ -e "$archive" ] || continue

    archive_no_ext=$(basename "$archive" .tgz)

    # Parse chart name from archive name
    chart_name=$(echo $archive_no_ext | sed -E 's|-[0-9]+\.[0-9]+\.[0-9]+.*||')
    if [[ $chart_name == *"$chart_suffix" ]]; then
        chart_name=${chart_name%$chart_suffix}
    fi

    # Parse chart version from archive name
    chart_version=$(echo $archive_no_ext | sed -E 's|.*-([0-9]+\.[0-9]+\.[0-9]+.*)$|\1|')
    
    # Encode the archive itself
    base64_encoded_archive=$(base64 $base64_option "$archive")

    # Construct the secret literal
    data_str="$chart_name|$chart_version|$base64_encoded_archive"

    secret_file=$(mktemp)
    echo -n "$data_str" > $secret_file

    # Create chart data secret in the 'eib-charts-upgrader' fleet
    secret_name=$(echo $archive_no_ext | tr '.' '-' | tr '+' '-')
    kubectl create secret generic $secret_name \
    --from-file=$secret_name.txt=$secret_file \
    --dry-run=client -o yaml > $secrets_path/$secret_name.yaml

    # Add the script name to the 'eib-charts-upgrader' fleet secret kustomization.yaml
    echo "- $secret_name.yaml" >> $secrets_kustomize

    # Add the secret name to the job.yaml patch file
    cat <<EOF >> $job_patch
      - secret:
          name: $secret_name
EOF
done

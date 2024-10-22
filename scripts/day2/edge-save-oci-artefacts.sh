#!/bin/bash
set -e

list="edge-release-helm-oci-artefacts.txt"
oci_archive="oci-artefacts.tar.gz"
source_registry=""
usage () {
    echo "USAGE: $0 [--artefact-list edge-helm-oci-artefacts.txt] [--archive oci-artefacts.tar.gz] --source-registry registry.suse.com"
    echo "  [-al|--artefact-list path] text file with list of images; one image per line."
    echo "  [-a|--archive] tar.gz archive containing OCI artefact archives that will be generated."
    echo "  [-s|--source-registry registry:port] source registry in the registry:port format."
    echo "  [-h|--help] Usage message"
}

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -s|--source-registry)
        source_registry="$2"
        shift # past argument
        shift # past value
        ;;
        -al|--artefact-list)
        list="$2"
        shift # past argument
        shift # past value
        ;;
        -a|--archive)
        oci_archive="$2"
        shift # past argument
        shift # past value
        ;;
        -h|--help)
        help="true"
        shift
        ;;
        *)
        usage
        exit 1
        ;;
    esac
done

if [[ $help ]]; then
    usage
    exit 0
fi

if [[ -z "${source_registry}" ]]; then
    usage
    exit 1
fi

case "${source_registry}" in
    oci://*)
        source_registry="${source_registry}/"
        ;;
    *)
        source_registry="oci://${source_registry}/"
        ;;
esac

if ! command -v "helm" &> /dev/null; then
    echo "Script requires 'helm' to load images into the target registry."
    echo "For 'helm' installation instructions, see https://helm.sh/docs/intro/install/"
    exit 1
fi


temp_dir=edge-release-oci-tgz-$(date +%Y%m%d)
mkdir ${temp_dir}
trap "rm -rf ${temp_dir}" EXIT

while IFS= read -r i; do
    [ -z "${i}" ] && continue

    arrI=(${i//:/ })
    if [[ ${#arrI[@]} -ne 2 ]]; then
        echo "Skipping incorrect entry: ${i}"
        continue
    fi
    
    helm pull ${source_registry}${arrI[0]} --version ${arrI[1]} --destination ${temp_dir}
done < "${list}"

tar -czvf ${oci_archive} ${temp_dir}

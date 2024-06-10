#!/bin/bash
set -e

source_registry=""
usage () {
    echo "USAGE: $0 --archive-directory edge-release-oci-tgz --source-registry registry.suse.com --registry my.registry.com:5000"
    echo "  [-ad|--archive-directory] directory from the 'edge-save-oci-artefacts.sh' generated '.tar.gz' archive"
    echo "  [-r|--registry registry:port] target private registry in the registry:port format."
    echo "  [-s|--source-registry registry:port] source registry in the registry:port format."
    echo "  [-h|--help] Usage message"
}

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -r|--registry)
        target_registry="$2"
        shift # past argument
        shift # past value
        ;;
        -s|--source-registry)
        source_registry="$2"
        shift # past argument
        shift # past value
        ;;
        -ad|--archive-directory)
        archive_dir="$2"
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

if [[ -z "${target_registry}" ]]; then
    usage
    exit 1
fi

if [[ -z "${source_registry}" ]]; then
    usage
    exit 1
fi

if [[ -z "${archive_dir}" ]]; then
    usage
    exit 1
fi

if [ ! -z "${source_registry}" ]; then
    source_registry="${source_registry}/"
fi

if ! command -v "helm" &> /dev/null; then
    echo "Script requires 'helm' to load images into the target registry."
    echo "For 'helm' installation instructions, see https://helm.sh/docs/intro/install/"
    exit 1
fi

for FILE in ${archive_dir}/*; do
    helm push ${FILE} oci://${target_registry}
done

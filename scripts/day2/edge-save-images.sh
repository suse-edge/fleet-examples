#!/bin/bash
list="edge-release-images.txt"
images="edge-images.tar.gz"
source_registry=""

usage () {
    echo "USAGE: $0 [--image-list edge-release-images.txt] [--images edge-images.tar.gz] --source-registry registry.suse.com"
    echo "  [-i|--images] tar.gz file to use with 'podman load'."
    echo "  [-s|--source-registry] source registry to pull images from in registry:port format e.g. registry.suse.com."
    echo "  [-l|--image-list path] text file with list of images; one image per line."
    echo "  [-h|--help] Usage message"
}

POSITIONAL=()
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -i|--images)
        images="$2"
        shift # past argument
        shift # past value
        ;;
        -s|--source-registry)
        if [ -z "${source_registry}" ]; then
            source_registry=$2
        else
            source_registry="${source_registry},$2"
        fi
        shift # past argument
        shift # past value
        ;;
        -l|--image-list)
        list="$2"
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

if [[ -z "${images}" ]]; then
    echo "Missing '-i|--images'"
    usage
    exit 1
fi

if [ -z "${source_registry}" ]; then
    echo "Must have at least one '-s|--source-registry' entry"
    usage
    exit 1
fi

pulled=""
while IFS= read -r i; do
    [ -z "${i}" ] && continue

    found=""
    img="${source_registry}/${i}"
    if podman pull "${img}" > /dev/null 2>&1; then
        echo "Image pull success: ${img}"
        pulled="${pulled} ${img}"
        found="true"
    elif podman inspect "${img}" > /dev/null 2>&1; then
        pulled="${pulled} ${img}"
    fi
    if [ ! -z "${found}" ]; then
        found=""
    else
        echo "Unable to find image '${i}' in the provided source-registries - '${source_registry}'"
    fi
done < "${list}"

echo "Creating ${images} with $(echo ${pulled} | wc -w | tr -d '[:space:]') images"
podman save $(echo ${pulled}) | gzip --stdout > ${images}

#!/bin/bash
list="edge-release-rke2-images.txt"

usage () {
    echo "USAGE: $0 [--image-list edge-release-images.txt] [--output-path ./]"
    echo "  [-o|--output-path path] folder to save all the images tarball"
    echo "  [-l|--image-list file] text file with list of images; one image per line."
    echo "  [-h|--help] Usage message"
}

POSITIONAL=()
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -o|--output-path)
        output="$2"
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

if [[ -z "${output}" ]]; then
    echo "Missing '-o|--output-path'"
    usage
    exit 1
fi

# check if exist folder ${output}
if [ ! -d "${output}" ]; then
		echo "Output folder does not exist. Please, create it first."
    usage
    exit 1
fi

cd "${output}" || exit 1

while IFS= read -r i; do
    [ -z "${i}" ] && continue
	  # download file using curl command
	  curl -LkO ${i}
done < "${list}"

echo "Creating ${output} with the rke2 images tarball"


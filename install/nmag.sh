#!/bin/bash

set -o errexit

# Check for required packages
PKGS="g++ libblas-dev libreadline-dev make m4 gawk zlib1g-dev readline-common liblapack-dev"
for pkg in $PKGS; do
    if ! dpkg -s $pkg > /dev/null 2>&1; then
	echo "Nmag needs the package $pkg. Trying to install it..."
	sudo apt-get install $pkg
    fi
done

# The default installation location is $HOME. Set
# the NMAG_PREFIX environment variable to change this.
NMAG_PREFIX=${NMAG_PREFIX:-$HOME}  # or maybe use NMAG_PREFIX=/usr/local ?

echo "Installing nmag in '$NMAG_PREFIX'. Set the NMAG_PREFIX environment variable to specify a different location."

# create installation directory if it doesn't exist
if ! [ -e ${NMAG_PREFIX} ]; then
   install -d ${NMAG_PREFIX};
   echo "Creating directory $NMAG_PREFIX";
fi

source="nmag-0.2.1"
TARBALLNAME="$source.tar.gz"
TARBALLURL="http://nmag.soton.ac.uk/nmag/0.2/download/all/$TARBALLNAME"

echo "Installing $source from $TARBALLURL"

echo "Changing directory to $NMAG_PREFIX"
cd $NMAG_PREFIX
echo "Working directory is `pwd`"
pwd

if [ ! -e $TARBALLNAME ]
then
    wget $TARBALLURL
fi

if [ ! -e $source ]
then
    tar xzvf $TARBALLNAME
fi

cd $source

make

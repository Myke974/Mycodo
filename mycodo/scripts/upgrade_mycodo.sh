#!/bin/bash
#
#  upgrade_mycodo.sh - Upgrade Mycodo to the latest version on GitHub
#
#  Copyright (C) 2015  Kyle T. Gabriel
#
#  This file is part of Mycodo
#
#  Mycodo is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Mycodo is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Mycodo. If not, see <http://www.gnu.org/licenses/>.
#
#  Contact at kylegabriel.com


if [ "$EUID" -ne 0 ]; then
    printf "Please run as root\n"
    exit
fi

INSTALL_DIRECTORY=$( cd "$( dirname "${BASH_SOURCE[0]}" )/../../" && pwd -P )
cd ${INSTALL_DIRECTORY}

case "${1:-''}" in
    'backup')
        NOW=$(date +"%Y-%m-%d_%H-%M-%S")
        CURCOMMIT=$(git rev-parse --short HEAD)
        printf "#### Creating backup /var/Mycodo-backups/Mycodo-$NOW-$CURCOMMIT from $INSTALL_DIRECTORY ####\n"
        mkdir -p /var/Mycodo-backups
        mkdir -p /var/Mycodo-backups/Mycodo-${NOW}-${CURCOMMIT}
        rsync -ah --stats \
            --exclude old \
            --exclude .git \
            --exclude src \
            --exclude camera-stills \
            --exclude camera-timelapse \
            --exclude camera-video \
            ${INSTALL_DIRECTORY}/ /var/Mycodo-backups/Mycodo-${NOW}-${CURCOMMIT}
    ;;
    'upgrade')
        echo "1" > ${INSTALL_DIRECTORY}/.updating
        NOW=$(date +"%m-%d-%Y %H:%M:%S")
        printf "#### Upgrade Initiated $NOW ####\n"

        printf "#### Checking for Upgrade ####\n"
        git fetch origin

        if git status -uno | grep 'Your branch is behind' > /dev/null; then
            git status -uno | grep 'Your branch is behind'
            printf "The remote git repository is newer than yours. This could mean there is an upgrade to Mycodo.\n"

            if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
                echo '1' > ${INSTALL_DIRECTORY}/.updating

                printf "#### Stopping Mycodo Daemon ####\n"
                service mycodo stop

                # Create backup
                /bin/bash ${INSTALL_DIRECTORY}/mycodo/scripts/upgrade_mycodo.sh backup

                printf "#### Updating From GitHub ####\n"
                git fetch
                git reset --hard origin/master

                printf "#### Executing Post-Upgrade Commands ####\n"
                if [ -f ${INSTALL_DIRECTORY}/mycodo/scripts/upgrade_post.sh ]; then
                    ${INSTALL_DIRECTORY}/mycodo/scripts/upgrade_post.sh
                    printf "#### End Post-Upgrade Commands ####\n"
                else
                    printf "Error: upgrade_post.sh not found\n"
                fi
                
                END=$(date +"%m-%d-%Y %H:%M:%S")
                printf "#### Upgrade Finished $END ####\n\n"

                echo '0' > ${INSTALL_DIRECTORY}/.updating
                exit 0
            else
                printf "Error: No git repository found. Upgrade stopped.\n\n"
                echo '0' > ${INSTALL_DIRECTORY}/.updating
                exit 1
            fi
        else
            printf "Your version of Mycodo is already the latest version.\n\n"
            echo '0' > ${INSTALL_DIRECTORY}/.updating
            exit 0
        fi
    ;;
    'upgrade-packages')
        printf "#### Installing prerequisite apt packages.\n"
        apt-get update -y
        apt-get install -y libav-tools libffi-dev libi2c-dev python-dev python-setuptools python-smbus sqlite3 gawk
        easy_install pip
    ;;
    'initialize')
        useradd -M mycodo
        adduser mycodo gpio
        adduser mycodo adm
        adduser mycodo video

        if [ ! -e ${INSTALL_DIRECTORY}/.updating ]; then
            echo '0' > ${INSTALL_DIRECTORY}/.updating
        fi
        chown -LR mycodo.mycodo ${INSTALL_DIRECTORY}
        ln -sf ${INSTALL_DIRECTORY}/ /var/www/mycodo

        mkdir -p /var/log/mycodo

        if [ ! -e /var/log/mycodo/mycodo.log ]; then
            touch /var/log/mycodo/mycodo.log
        fi
        
        if [ ! -e /var/log/mycodo/mycodoupgrade.log ]; then
            touch /var/log/mycodo/mycodoupgrade.log
        fi

        if [ ! -e /var/log/mycodo/mycodorestore.log ]; then
            touch /var/log/mycodo/mycodorestore.log
        fi

        if [ ! -e /var/log/mycodo/login.log ]; then
            touch /var/log/mycodo/login.log
        fi

        chown -R mycodo.mycodo /var/log/mycodo

        find ${INSTALL_DIRECTORY}/ -type d -exec chmod u+wx,g+wx {} +
        find ${INSTALL_DIRECTORY}/ -type f -exec chmod u+w,g+w,o+r {} +
        # find $INSTALL_DIRECTORY/mycodo -type f -name '.?*' -prune -o -exec chmod 770 {} +
        chown root:mycodo ${INSTALL_DIRECTORY}/mycodo/scripts/mycodo_wrapper
        chmod 4770 ${INSTALL_DIRECTORY}/mycodo/scripts/mycodo_wrapper
    ;;
esac

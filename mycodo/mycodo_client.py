#!/usr/bin/python
# -*- coding: utf-8 -*-
#
#  mycodo_client.py - Client for mycodo daemon. Communicates with daemon
#                     to execute commands and receive status.
#
#  Copyright (C) 2016  Kyle T. Gabriel
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

import logging
import argparse
import datetime
import rpyc
import socket
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s %(message)s'
)
logger = logging.getLogger(__name__)


class DaemonControl:
    """
    Communicate with the daemon to execute commands or retrieve information.

    """
    def __init__(self):
        try:
            self.rpyc_client = rpyc.connect("localhost", 18813)
        except socket.error:
            logger.info("Connection refused. Is the daemon running?")
            raise Exception("Connection refused. Is the daemon running?")

    def flash_lcd(self, lcd_id, state):
        return self.rpyc_client.root.flash_lcd(lcd_id, state)

    def relay_state(self, relay_id):
        return self.rpyc_client.root.relay_state(relay_id)

    def relay_on_off(self, relay_id, state, duration):
        if state == 'on':
            self.relay_on(relay_id, duration)
        else:
            self.relay_off(relay_id)

    def relay_on(self, relay_id, duration):
        return self.rpyc_client.root.relay_on(relay_id, duration)

    def relay_off(self, relay_id):
        return self.rpyc_client.root.relay_off(relay_id)

    def add_relay(self, relay_id):
        return self.rpyc_client.root.add_relay(relay_id)

    def mod_relay(self, relay_id, setup_pin):
        return self.rpyc_client.root.mod_relay(relay_id, setup_pin)

    def del_relay(self, relay_id):
        return self.rpyc_client.root.del_relay(relay_id)

    def activate_controller(self, controller_type, controller_id):
        return self.rpyc_client.root.activate_controller(
            controller_type, controller_id)

    def deactivate_controller(self, controller_type, controller_id):
        return self.rpyc_client.root.deactivate_controller(
            controller_type, controller_id)

    def refresh_sensor_conditionals(self, sensor_id, cond_mod, cond_id):
        return self.rpyc_client.root.refresh_sensor_conditionals(
            sensor_id, cond_mod, cond_id)

    def daemon_status(self):
        return self.rpyc_client.root.daemon_status()

    def pid_mod(self, pid_id):
        return self.rpyc_client.root.pid_mod(pid_id)

    def pid_hold(self, pid_id):
        return self.rpyc_client.root.pid_hold(pid_id)

    def pid_pause(self, pid_id):
        return self.rpyc_client.root.pid_pause(pid_id)

    def pid_resume(self, pid_id):
        return self.rpyc_client.root.pid_resume(pid_id)

    def terminate_daemon(self):
        return self.rpyc_client.root.terminate_daemon()


def parseargs(parser):
    parser.add_argument('--activatecontroller', nargs=2,
                        metavar=('CONTROLLER', 'ID'), type=str,
                        help='Activate controller. Options: LCD, PID, Sensor, Timer',
                        required=False)
    parser.add_argument('--deactivatecontroller', nargs=2,
                        metavar=('CONTROLLER', 'ID'), type=str,
                        help='Deactivate controller. Options: LCD, PID, Sensor, Timer',
                        required=False)
    parser.add_argument('--relayoff', metavar='RELAYID', type=str,
                        help='Turn off relay with relay ID',
                        required=False)
    parser.add_argument('--relayon', metavar='RELAYID', type=str,
                        help='Turn on relay with relay ID',
                        required=False)
    parser.add_argument('--duration', metavar='SECONDS', type=float,
                        help='Turn on relay for a duration of time (seconds)',
                        required=False)
    parser.add_argument('-t', '--terminate', action='store_true',
                        help="Terminate the daemon")

    return parser.parse_args()


if __name__ == "__main__":
    now = datetime.datetime.now
    parser = argparse.ArgumentParser(description="Client for Mycodo daemon.")
    args = parseargs(parser)
    daemon_control = DaemonControl()

    if args.relayoff:
        return_msg = daemon_control.relay_off(args.relayoff)
        logger.info("[Remote command] Turn off relay with ID '{id}': "
                    "Server returned: {msg}".format(
                        id=args.relayoff,
                        msg=return_msg))

    if args.duration and args.relayon is None:
        parser.error("--duration requires --relayon")

    if args.relayon:
        duration = 0
        if args.duration:
            duration = args.duration
        return_msg = daemon_control.relay_on(args.relayon, duration)
        logger.info("[Remote command] Turn on relay with ID '{id}': "
                    "Server returned:".format(
                        id=args.relayon,
                        msg=return_msg))

    if args.activatecontroller:
        if args.activatecontroller[0] not in ['LCD', 'Log', 'PID',
                                              'Sensor', 'Timer']:
            logger.info("Invalid controller type. Options are LCD, Log, PID, "
                        "Sensor, and Timer.")
        else:
            return_msg = daemon_control.activate_controller(
                args.activatecontroller[0], args.activatecontroller[1])
            logger.info("[Remote command] Activate {type} controller with "
                        "ID '{id}': Server returned: {msg}".format(
                            type=args.activatecontroller[0],
                            id=args.activatecontroller[1],
                            msg=return_msg))

    if args.deactivatecontroller:
        if args.deactivatecontroller[0] not in ['LCD', 'Log', 'PID',
                                                'Sensor', 'Timer']:
            logger.info("Invalid controller type. Options are LCD, Log, PID, "
                        "Sensor, and Timer.")
        else:
            return_msg = daemon_control.deactivate_controller(
                args.deactivatecontroller[0], args.deactivatecontroller[1])
            logger.info("[Remote command] Deactivate {type} controller with "
                        "ID '{id}': Server returned: {msg}".format(
                            type=args.deactivatecontroller[0],
                            id=args.deactivatecontroller[1],
                            msg=return_msg))

    if args.terminate:
        logger.info("[Remote command] Terminate daemon...")
        if daemon_control.terminate_daemon():
            logger.info("Daemon response: Terminated.")
        else:
            logger.info("Unknown daemon response.")

    sys.exit(0)

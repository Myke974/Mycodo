#!/usr/bin/python
# -*- coding: utf-8 -*-
#
import logging
import bcrypt
import os
import random
import requests
from RPi import GPIO
import sqlalchemy
import string
import time
from collections import OrderedDict
from datetime import datetime
import functools

import gzip
from cStringIO import StringIO as IO

from flask import (current_app,
                   flash,
                   request,
                   session,
                   redirect,
                   after_this_request)
from flask_babel import gettext

from influxdb import InfluxDBClient
from sqlalchemy import and_

from utils.send_data import send_email
from databases.mycodo_db.models import CameraStill
from databases.mycodo_db.models import DisplayOrder
from databases.mycodo_db.models import Graph
from databases.mycodo_db.models import LCD
from databases.mycodo_db.models import Log
from databases.mycodo_db.models import Method
from databases.mycodo_db.models import Misc
from databases.mycodo_db.models import PID
from databases.mycodo_db.models import Relay
from databases.mycodo_db.models import RelayConditional
from databases.mycodo_db.models import Remote
from databases.mycodo_db.models import SMTP
from databases.mycodo_db.models import Sensor
from databases.mycodo_db.models import SensorConditional
from databases.mycodo_db.models import Timer
from databases.users_db.models import Users
from databases.utils import session_scope
from scripts.utils import test_username, test_password
from mycodo_client import DaemonControl

from config import DAEMON_PID_FILE
from config import INSTALL_DIRECTORY
from config import INFLUXDB_HOST
from config import INFLUXDB_PORT
from config import INFLUXDB_USER
from config import INFLUXDB_PASSWORD
from config import INFLUXDB_DATABASE

logger = logging.getLogger(__name__)


#
# Method Development
#

def is_positive_integer(number_string):
    try:
        if int(number_string) < 0:
            flash(gettext("Duration must be a positive integer"), "error")
            return False
    except ValueError:
        flash(gettext("Duration must be a valid integer"), "error")
        return False
    return True


def validate_method_data(form_data, this_method):
    if form_data.method_select.data == 'setpoint':
        if this_method.method_type == 'Date':
            if (not form_data.startTime.data or
                    not form_data.endTime.data or
                    form_data.startSetpoint.data == ''):
                flash(gettext("Required: Start date/time, end date/time, "
                              "start setpoint"), "error")
                return 1
            try:
                start_time = datetime.strptime(form_data.startTime.data,
                                               '%Y-%m-%d %H:%M:%S')
                end_time = datetime.strptime(form_data.endTime.data,
                                             '%Y-%m-%d %H:%M:%S')
            except ValueError:
                flash(gettext("Invalid Date/Time format. Correct format: "
                              "DD/MM/YYYY HH:MM:SS"), "error")
                return 1
            if end_time <= start_time:
                flash(gettext("The end time/date must be after the start "
                              "time/date."), "error")
                return 1

        elif this_method.method_type == 'Daily':
            if (not form_data.startDailyTime.data or
                    not form_data.endDailyTime.data or
                    form_data.startSetpoint.data == ''):
                flash(gettext("Required: Start time, end time, start "
                              "setpoint"), "error")
                return 1
            try:
                start_time = datetime.strptime(form_data.startDailyTime.data,
                                               '%H:%M:%S')
                end_time = datetime.strptime(form_data.endDailyTime.data,
                                             '%H:%M:%S')
            except ValueError:
                flash(gettext("Invalid Date/Time format. Correct format: "
                              "HH:MM:SS"), "error")
                return 1
            if end_time <= start_time:
                flash(gettext("The end time must be after the start time."),
                      "error")
                return 1

        elif this_method.method_type == 'Duration':
            if (not form_data.DurationSec.data or
                    form_data.startSetpoint.data == ''):
                flash(gettext("Required: Duration, start setpoint"),
                      "error")
                return 1
            if not is_positive_integer(form_data.DurationSec.data):
                return 1

    elif form_data.method_select.data == 'relay':
        if this_method.method_type == 'Date':
            if (not form_data.relayTime.data or
                    not form_data.relayID.data or
                    not form_data.relayState.data):
                flash(gettext("Required: Date/Time, Relay ID, and Relay "
                              "State"), "error")
                return 1
            try:
                start_time = datetime.strptime(form_data.relayTime.data,
                                               '%Y-%m-%d %H:%M:%S')
            except ValueError:
                flash(gettext("Invalid Date/Time format. Correct format: "
                              "DD-MM-YYYY HH:MM:SS"), "error")
                return 1
        elif this_method.method_type == 'Duration':
            if (not form_data.DurationSec.data or
                    not form_data.relayID.data or
                    not form_data.relayState.data):
                flash(gettext("Required: Duration, Relay ID, and Relay State"),
                      "error")
                return 1
            if not is_positive_integer(form_data.DurationSec.data):
                return 1
        elif this_method.method_type == 'Daily':
            if (not form_data.relayDailyTime.data or
                    not form_data.relayID.data or
                    not form_data.relayState.data):
                flash(gettext("Required: Time, Relay ID, and Relay State"),
                      "error")
                return 1
            try:
                start_time = datetime.strptime(form_data.relayDailyTime.data,
                                               '%H:%M:%S')
            except ValueError:
                flash(gettext("Invalid Date/Time format. Correct format: "
                              "HH:MM:SS"), "error")
                return 1


def method_create(formCreateMethod, method_id):
    if deny_guest_user():
        return redirect('/')

    try:
        new_method = Method()
        random_id = ''.join([random.choice(
            string.ascii_letters + string.digits) for n in xrange(8)])
        new_method.id = random_id
        new_method.method_id = method_id
        if not formCreateMethod.name.data:
            new_method.name = "New Method"
        else:
            new_method.name = formCreateMethod.name.data
        new_method.method_type = formCreateMethod.method_type.data
        if formCreateMethod.method_type.data == 'DailySine':
            new_method.amplitude = 1.0
            new_method.frequency = 1.0
            new_method.shift_angle = 0.0
            new_method.shift_y = 1.0
        if formCreateMethod.method_type.data == 'DailyBezier':
            new_method.shift_angle = 0.0
            new_method.x0 = 20.0
            new_method.y0 = 20.0
            new_method.x1 = 10.0
            new_method.y1 = 13.5
            new_method.x2 = 22.5
            new_method.y2 = 30.0
            new_method.x3 = 0.0
            new_method.y3 = 20.0
        new_method.method_order = 0
        new_method.controller_type = formCreateMethod.controller_type.data
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            db_session.add(new_method)
    except Exception as except_msg:
        flash(gettext("Method Error: %(err)s", err=except_msg), "error")


def method_add(formAddMethod, method):
    if deny_guest_user():
        return redirect('/')

    try:
        # Validate input time data
        this_method = method.filter(Method.method_id == formAddMethod.method_id.data)
        this_method = this_method.filter(Method.method_order == 0).first()
        if validate_method_data(formAddMethod, this_method):
            return 1

        if this_method.method_type == 'DailySine':
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_method = db_session.query(Method).filter(
                    Method.method_id == formAddMethod.method_id.data).first()
                mod_method.amplitude = formAddMethod.amplitude.data
                mod_method.frequency = formAddMethod.frequency.data
                mod_method.shift_angle = formAddMethod.shiftAngle.data
                mod_method.shift_y = formAddMethod.shiftY.data
                db_session.commit()
            return 0

        if this_method.method_type == 'DailyBezier':
            if not 0 <= formAddMethod.shiftAngle.data <= 360:
                flash(gettext("Error: Angle Shift is out of range. It must be "
                              "<= 0 and <= 360."), "error")
                return 1
            if formAddMethod.x0.data <= formAddMethod.x3.data:
                flash(gettext("Error: X0 must be greater than X3."), "error")
                return 1
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_method = db_session.query(Method).filter(
                    Method.method_id == formAddMethod.method_id.data).first()
                mod_method.shift_angle = formAddMethod.shiftAngle.data
                mod_method.x0 = formAddMethod.x0.data
                mod_method.y0 = formAddMethod.y0.data
                mod_method.x1 = formAddMethod.x1.data
                mod_method.y1 = formAddMethod.y1.data
                mod_method.x2 = formAddMethod.x2.data
                mod_method.y2 = formAddMethod.y2.data
                mod_method.x3 = formAddMethod.x3.data
                mod_method.y3 = formAddMethod.y3.data
                db_session.commit()
            return 0

        if formAddMethod.method_select.data == 'setpoint':
            if this_method.method_type == 'Date':
                start_time = datetime.strptime(formAddMethod.startTime.data,
                                               '%Y-%m-%d %H:%M:%S')
                end_time = datetime.strptime(formAddMethod.endTime.data,
                                             '%Y-%m-%d %H:%M:%S')
            elif this_method.method_type == 'Daily':
                start_time = datetime.strptime(formAddMethod.startDailyTime.data,
                                               '%H:%M:%S')
                end_time = datetime.strptime(formAddMethod.endDailyTime.data,
                                             '%H:%M:%S')

            if this_method.method_type in ['Date', 'Daily']:
                # Check if the start time comes after the last entry's end time
                last_method = method.filter(Method.method_id == this_method.method_id)
                last_method = last_method.filter(Method.method_order > 0)
                last_method = last_method.filter(Method.relay_id == None)
                last_method = last_method.order_by(Method.method_order.desc()).first()
                if last_method is not None:
                    if this_method.method_type == 'Date':
                        last_method_end_time = datetime.strptime(last_method.end_time,
                                                                 '%Y-%m-%d %H:%M:%S')
                    elif this_method.method_type == 'Daily':
                        last_method_end_time = datetime.strptime(last_method.end_time,
                                                                 '%H:%M:%S')

                    if start_time < last_method_end_time:
                        flash(gettext("The new entry start time (%(st)s) "
                                      "cannot overlap the last entry's end "
                                      "time (%(et)s). Note: They may be the "
                                      "same time.",
                                      st=last_method_end_time,
                                      et=start_time),
                              "error")
                        return 1

        elif formAddMethod.method_select.data == 'relay':
            if this_method.method_type == 'Date':
                start_time = datetime.strptime(formAddMethod.relayTime.data,
                                               '%Y-%m-%d %H:%M:%S')
            elif this_method.method_type == 'Daily':
                start_time = datetime.strptime(formAddMethod.relayDailyTime.data,
                                               '%H:%M:%S')

        new_method = Method()
        random_id = ''.join([random.choice(
            string.ascii_letters + string.digits) for n in xrange(8)])
        new_method.id = random_id
        new_method.method_id = formAddMethod.method_id.data

        # Get last number in ordered list, incrememnt for new entry
        method_last = method.order_by(Method.method_order.desc()).first()
        new_method.method_order = method_last.method_order+1

        if this_method.method_type == 'Date':
            if formAddMethod.method_select.data == 'setpoint':
                new_method.start_time = start_time.strftime('%Y-%m-%d %H:%M:%S')
                new_method.end_time = end_time.strftime('%Y-%m-%d %H:%M:%S')
            if formAddMethod.method_select.data == 'relay':
                new_method.start_time = formAddMethod.relayTime.data
        elif this_method.method_type == 'Daily':
            if formAddMethod.method_select.data == 'setpoint':
                new_method.start_time = start_time.strftime('%H:%M:%S')
                new_method.end_time = end_time.strftime('%H:%M:%S')
            if formAddMethod.method_select.data == 'relay':
                new_method.start_time = formAddMethod.relayDailyTime.data
        elif this_method.method_type == 'Duration':
            new_method.duration_sec = formAddMethod.DurationSec.data

        if formAddMethod.method_select.data == 'setpoint':
            new_method.start_setpoint = formAddMethod.startSetpoint.data
            new_method.end_setpoint = formAddMethod.endSetpoint.data
        elif formAddMethod.method_select.data == 'relay':
            new_method.relay_id = formAddMethod.relayID.data
            new_method.relay_state = formAddMethod.relayState.data
            new_method.relay_duration = formAddMethod.relayDurationSec.data

        new_method.repeat = ''
        new_method.repeat_duration = ''
        new_method.repeat_units = ''
        new_method.total_runs = ''

        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            db_session.add(new_method)

        if formAddMethod.method_select.data == 'setpoint':
            if this_method.method_type == 'Date':
                flash(gettext("Added duration to method from %(st)s to "
                              "%(end)s", st=start_time, end=end_time),
                      "success")
            elif this_method.method_type == 'Daily':
                flash(gettext("Added duration to method from %(st)s to "
                              "%(end)s",
                              st=start_time.strftime('%H:%M:%S'),
                              end=end_time.strftime('%H:%M:%S')),
                      "success")
            elif this_method.method_type == 'Duration':
                flash(gettext("Added duration to method for %(sec)s seconds",
                              sec=formAddMethod.DurationSec.data), "success")
        elif formAddMethod.method_select.data == 'relay':
            if this_method.method_type == 'Date':
                flash(gettext("Added relay modulation to method at start "
                              "time: %(tm)s", tm=start_time), "success")
            elif this_method.method_type == 'Daily':
                flash(gettext("Added relay modulation to method at start "
                              "time: %(tm)s",
                              tm=start_time.strftime('%H:%M:%S')), "success")
            elif this_method.method_type == 'Duration':
                flash(gettext("Added relay modulation to method at start "
                              "time: %(tm)s",
                              tm=formAddMethod.DurationSec.data), "success")

    except Exception as err:
        flash(gettext("Method Error: %(err)s", err=err), "error")


def method_mod(formModMethod, method):
    if deny_guest_user():
        return redirect('/')

    try:
        if formModMethod.Delete.data:
            delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                                 Method,
                                 formModMethod.method_id.data)
            return 0

        if formModMethod.name.data:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_method = db_session.query(Method).filter(
                    Method.method_id == formModMethod.method_id.data)
                mod_method = mod_method.filter(Method.method_order == 0).first()
                mod_method.name = formModMethod.name.data
                db_session.commit()
                return 0

        # Ensure data data is valid
        this_method = method.filter(Method.id == formModMethod.method_id.data).first()
        method_set = method.filter(Method.method_id == this_method.method_id)
        method_set = method_set.filter(Method.method_order == 0).first()
        if validate_method_data(formModMethod, method_set):
            return 1

        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            mod_method = db_session.query(Method).filter(
                Method.id == formModMethod.method_id.data).first()

            if formModMethod.method_select.data == 'setpoint':
                if method_set.method_type == 'Date':
                    start_time = datetime.strptime(formModMethod.startTime.data, '%Y-%m-%d %H:%M:%S')
                    end_time = datetime.strptime(formModMethod.endTime.data, '%Y-%m-%d %H:%M:%S')

                    # Ensure the start time comes after the previous entry's end time
                    # and the end time comes before the next entry's start time
                    # method_id_set is the id given to all method entries, 'method_id', not 'id'

                    method_filtered = method.filter(Method.method_order > 0)
                    method_filtered = method_filtered.filter(Method.method_id == this_method.method_id)

                    previous_method = method.order_by(Method.method_order.desc()).filter(
                        Method.method_order < this_method.method_order).first()
                    next_method = method.order_by(Method.method_order.asc()).filter(
                        Method.method_order > this_method.method_order).first()

                    if previous_method is not None and previous_method.end_time is not None:
                        previous_end_time = datetime.strptime(previous_method.end_time,
                            '%Y-%m-%d %H:%M:%S')
                        if previous_end_time is not None and start_time < previous_end_time:
                            flash(gettext("The entry start time (%(st)s) cannot "
                                          "overlap the previous entry's end time "
                                          "(%(et)s)",
                                          st=start_time, et=previous_end_time),
                                  "error")
                            return 1

                    if next_method is not None and next_method.start_time is not None:
                        next_start_time = datetime.strptime(next_method.start_time,
                            '%Y-%m-%d %H:%M:%S')
                        if next_start_time is not None and end_time > next_start_time:
                            flash(gettext("The entry end time (%(et)s) cannot overlap "
                                          "the next entry's start time (%(st)s)",
                                          et=end_time, st=next_start_time),
                                  "error")
                            return 1

                    mod_method.start_time = start_time.strftime('%Y-%m-%d %H:%M:%S')
                    mod_method.end_time = end_time.strftime('%Y-%m-%d %H:%M:%S')

                elif method_set.method_type == 'Duration':
                    mod_method.duration_sec = formModMethod.DurationSec.data

                elif method_set.method_type == 'Daily':
                    mod_method.start_time = formModMethod.startDailyTime.data
                    mod_method.end_time = formModMethod.endDailyTime.data

                mod_method.start_setpoint = formModMethod.startSetpoint.data
                mod_method.end_setpoint = formModMethod.endSetpoint.data

            elif formModMethod.method_select.data == 'relay':
                if method_set.method_type == 'Date':
                    mod_method.start_time = formModMethod.relayTime.data
                elif method_set.method_type == 'Duration':
                    mod_method.duration_sec = formModMethod.DurationSec.data
                mod_method.relay_id = formModMethod.relayID.data
                mod_method.relay_state = formModMethod.relayState.data
                mod_method.relay_duration = formModMethod.relayDurationSec.data

            elif method_set.method_type == 'DailySine':
                if formModMethod.method_select.data == 'relay':
                    mod_method.start_time = formModMethod.relayTime.data
                    mod_method.relay_id = formModMethod.relayID.data
                    mod_method.relay_state = formModMethod.relayState.data
                    mod_method.relay_duration = formModMethod.relayDurationSec.data

            db_session.commit()

    except Exception as err:
        flash(gettext("Method Error: %(err)s", err=err), "error")


def method_del(method_id):
    try:
        delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                             Method,
                             method_id)
    except Exception as err:
        flash(gettext("Method Error: %(err)s", err=err), "error")


#
# Authenticate remote hosts
#

def check_new_credentials(address, user, passw):
    credentials = {
        'user': user,
        'passw': passw
    }
    url = 'https://{}/newremote/'.format(address)
    try:
        r = requests.get(url, params=credentials, verify=False)
        return r.json()
    except Exception as msg:
        return {
            'status': 1,
            'message': "Error connecting to host: {}".format(msg)
        }


def auth_credentials(address, user, password_hash):
    credentials = {
        'user': user,
        'pw_hash': password_hash
    }
    url = 'https://{}/auth/'.format(address)
    try:
        r = requests.get(url, params=credentials, verify=False)
        return int(r.text)
    except Exception as e:
        logger.error("'auth_credentials' raised an exception: "
                     "{err}".format(err=e))
        return 1


def remote_host_add(formSetup, display_order):
    if deny_guest_user():
        return redirect('/')

    if formSetup.validate():
        try:
            pw_check = check_new_credentials(formSetup.host.data, formSetup.username.data, formSetup.password.data)
            if pw_check['status']:
                flash(pw_check['message'], "error")
                return 1
            new_remote_host = Remote()
            random_id = ''.join([random.choice(
                    string.ascii_letters + string.digits) for n in xrange(8)])
            new_remote_host.id = random_id
            new_remote_host.activated = 0
            new_remote_host.host = formSetup.host.data
            new_remote_host.username = formSetup.username.data
            new_remote_host.password_hash = pw_check['message']
            try:
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    db_session.add(new_remote_host)
                flash(gettext("Remote Host %(host)s with ID %(id)s "
                              "successfully added",
                              host=formSetup.host.data, id=random_id),
                      "success")
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_remote = db_session.query(DisplayOrder).first()
                    display_order.append(random_id)
                    order_remote.remote_host = ','.join(display_order)
                    db_session.commit()
            except sqlalchemy.exc.OperationalError as except_msg:
                flash(gettext("Remote Host Error: %(err)s", err=except_msg),
                      "error")
            except sqlalchemy.exc.IntegrityError as except_msg:
                flash(gettext("Remote Host Error: %(err)s", err=except_msg),
                      "error")
        except Exception as except_msg:
            flash(gettext("Remote Host Error: %(err)s", err=except_msg),
                  "error")
    else:
        flash_form_errors(formSetup)


def remote_host_del(formSetup, display_order):
    if deny_guest_user():
        return redirect('/')

    try:
        delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                             Remote,
                             formSetup.remote_id.data)
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            order_remote = db_session.query(DisplayOrder).first()
            display_order.remove(formSetup.remote_id.data)
            order_remote.remote_host = ','.join(display_order)
            db_session.commit()
    except Exception as except_msg:
        flash(gettext("Remote Host Error: %(err)s", err=except_msg), "error")


#
# Manipulate relay settings while daemon is running
#

def manipulate_relay(relay_id, action):
    """
    Add, delete, and modify relay settings while the daemon is active

    :param relay_id: relay ID in the SQL database
    :type relay_id: str
    :param action: add, del, or mod
    :type action: str
    """
    control = DaemonControl()
    if action == 'add':
        return_values = control.add_relay(relay_id)
    elif action == 'mod':
        return_values = control.mod_relay(relay_id)
    elif action == 'del':
        return_values = control.del_relay(relay_id)
    if return_values[0]:
        flash("{}".format(return_values[1]), "error")


#
# Activate/deactivate controller
#

def activate_deactivate_controller(controller_action,
                                   controller_type,
                                   controller_id):
    """
    Activate or deactivate controller

    :param controller_action: Activate or deactivate
    :type controller_action: str
    :param controller_type: The controller type (LCD, Log, PID, Sensor, Timer)
    :type controller_type: str
    :param controller_id: Controller with ID to activate or deactivate
    :type controller_id: str
    """
    if deny_guest_user():
        return redirect('/')

    if controller_action == 'activate':
        activated_integer = 1
    else:
        activated_integer = 0

    translated_names = {
        "LCD": gettext("LCD"),
        "Log": gettext("Log"),
        "PID": gettext("PID"),
        "Sensor": gettext("Sensor"),
        "Timer": gettext("Timer")
    }

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            if controller_type == 'LCD':
                mod_controller = db_session.query(LCD).filter(
                    LCD.id == controller_id).first()
            elif controller_type == 'Log':
                mod_controller = db_session.query(Log).filter(
                    Log.id == controller_id).first()
            elif controller_type == 'PID':
                mod_controller = db_session.query(PID).filter(
                    PID.id == controller_id).first()
            elif controller_type == 'Sensor':
                mod_controller = db_session.query(Sensor).filter(
                    Sensor.id == controller_id).first()
            elif controller_type == 'Timer':
                mod_controller = db_session.query(Timer).filter(
                    Timer.id == controller_id).first()
            mod_controller.activated = activated_integer
            db_session.commit()
        if activated_integer:
            flash(gettext("%(cont)s controller activated in SQL database",
                          cont=translated_names[controller_type]),
                  "success")
        else:
            flash(gettext("%(cont)s controller deactivated in SQL database",
                          cont=translated_names[controller_type]),
                  "success")
    except Exception as except_msg:
        flash(gettext("%(cont)s controller settings were not able to be "
                      "modified: %(err)s",
                      cont=translated_names[controller_type], err=except_msg),
              "error")

    try:
        control = DaemonControl()
        if controller_action == 'activate':
            return_values = control.activate_controller(controller_type,
                                                        controller_id)
        else:
            return_values = control.deactivate_controller(controller_type,
                                                          controller_id)
        if return_values[0]:
            flash("{}".format(return_values[1]), "error")
        else:
            flash("{}".format(return_values[1]), "success")
    except Exception as except_msg:
        flash(gettext("Could not communicate with daemon: %(err)s",
                      err=except_msg),
              "error")


#
# Choices
#

# return a dictionary of all available measurements
# Used to produce a multiselect form input for creating/modifying custom graphs
def choices_sensors(sensor):
    choices = OrderedDict()
    # populate form multiselect choices for sensors and mesaurements
    for each_sensor in sensor:
        if each_sensor.device in ['RPiCPULoad']:
            value = '{},cpu_load_1m'.format(each_sensor.id)
            display = '{} ({}) CPU Load (1m)'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
            value = '{},cpu_load_5m'.format(each_sensor.id)
            display = '{} ({}) CPU Load (5m)'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
            value = '{},cpu_load_15m'.format(each_sensor.id)
            display = '{} ({}) CPU Load (15m)'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device == 'CHIRP':
            value = '{},moisture'.format(each_sensor.id)
            display = '{} ({}) Moisture'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device in ['AM2315', 'ATLAS_PT1000', 'BME280', 'BMP',
                                  'CHIRP', 'DHT11', 'DHT22', 'DS18B20',
                                  'HTU21D', 'RPi', 'SHT1x_7x', 'SHT2x']:
            value = '{},temperature'.format(each_sensor.id)
            display = '{} ({}) Temperature'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device == 'TMP006':
            value = '{},temperature_object'.format(each_sensor.id)
            display = '{} ({}) Temperature (Object)'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
            value = '{},temperature_die'.format(each_sensor.id)
            display = '{} ({}) Temperature (Die)'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device in ['AM2315', 'BME280', 'DHT11', 'DHT22', 'HTU21D',
                                  'SHT1x_7x', 'SHT2x']:
            value = '{},humidity'.format(each_sensor.id)
            display = '{} ({}) Humidity'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
            value = '{},dewpoint'.format(each_sensor.id)
            display = '{} ({}) Dew Point'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device == 'K30':
            value = '{},co2'.format(each_sensor.id)
            display = '{} ({}) CO2'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device in ['BME280', 'BMP']:
            value = '{},pressure'.format(each_sensor.id)
            display = '{} ({}) Pressure'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
            value = '{},altitude'.format(each_sensor.id)
            display = '{} ({}) Altitude'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device == 'EDGE':
            value = '{},edge'.format(each_sensor.id)
            display = '{} ({}) Edge'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
        if each_sensor.device in ['ADS1x15', 'MCP342x']:
            value = '{},voltage'.format(each_sensor.id)
            display = '{} ({}) Volts'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
            value = '{},{}'.format(each_sensor.id, each_sensor.adc_measure)
            display = '{} ({}) {}'.format(
                each_sensor.id, each_sensor.name, each_sensor.adc_measure)
            choices.update({value:display})
        if each_sensor.device in ['CHIRP', 'TSL2561']:
            value = '{},lux'.format(each_sensor.id)
            display = '{} ({}) Lux'.format(
                each_sensor.id, each_sensor.name)
            choices.update({value:display})
    return choices


# Return a dictionary of all available ids and names
# produce a multiselect form input for creating/modifying custom graphs
def choices_id_name(table):
    choices = OrderedDict()
    # populate form multiselect choices for relays
    for each_entry in table:
        value = each_entry.id
        display = '{} ({})'.format(each_entry.id, each_entry.name)
        choices.update({value:display})
    return choices


#
# Graph
#

def graph_add(formAddGraph, display_order):
    if deny_guest_user():
        return redirect('/graph')

    if (formAddGraph.name.data and formAddGraph.width.data and
            formAddGraph.height.data and formAddGraph.xAxisDuration.data and
            formAddGraph.refreshDuration.data):
        new_graph = Graph()
        random_id = ''.join([random.choice(
                string.ascii_letters + string.digits) for n in xrange(8)])
        new_graph.id = random_id
        new_graph.name = formAddGraph.name.data
        pidIDs_joined = ",".join(formAddGraph.pidIDs.data)
        new_graph.pid_ids = pidIDs_joined
        relayIDs_joined = ",".join(formAddGraph.relayIDs.data)
        new_graph.relay_ids = relayIDs_joined
        sensorIDs_joined = ";".join(formAddGraph.sensorIDs.data)
        new_graph.sensor_ids = sensorIDs_joined
        new_graph.width = formAddGraph.width.data
        new_graph.height = formAddGraph.height.data
        new_graph.x_axis_duration = formAddGraph.xAxisDuration.data
        new_graph.refresh_duration = formAddGraph.refreshDuration.data
        new_graph.enable_navbar = formAddGraph.enableNavbar.data
        new_graph.enable_rangeselect = formAddGraph.enableRangeSelect.data
        new_graph.enable_export = formAddGraph.enableExport.data
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                db_session.add(new_graph)
            flash(gettext("Graph with ID %(id)s successfully created",
                          id=random_id),
                  "success")
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_graph = db_session.query(DisplayOrder).first()
                display_order.append(random_id)
                order_graph.graph = ','.join(display_order)
                db_session.commit()
                return 1
        except sqlalchemy.exc.OperationalError as except_msg:
            flash(gettext("Graph Error: %(err)s", err=except_msg), "error")
        except sqlalchemy.exc.IntegrityError as except_msg:
            flash(gettext("Graph Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddGraph)


def graph_mod(formModGraph):
    if deny_guest_user():
        return redirect('/graph')

    if formModGraph.validate():
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_graph = db_session.query(Graph).filter(
                    Graph.id == formModGraph.graph_id.data).first()
                mod_graph.name = formModGraph.name.data
                pidIDs_joined = ",".join(formModGraph.pidIDs.data)
                mod_graph.pid_ids = pidIDs_joined
                relayIDs_joined = ",".join(formModGraph.relayIDs.data)
                mod_graph.relay_ids = relayIDs_joined
                sensorIDs_joined = ";".join(formModGraph.sensorIDs.data)
                mod_graph.sensor_ids = sensorIDs_joined
                mod_graph.width = formModGraph.width.data
                mod_graph.height = formModGraph.height.data
                mod_graph.x_axis_duration = formModGraph.xAxisDuration.data
                mod_graph.refresh_duration = formModGraph.refreshDuration.data
                mod_graph.enable_navbar = formModGraph.enableNavbar.data
                mod_graph.enable_export = formModGraph.enableExport.data
                mod_graph.enable_rangeselect = formModGraph.enableRangeSelect.data
                db_session.commit()
        except sqlalchemy.exc.OperationalError as except_msg:
            flash(gettext("Graph Error: %(err)s", err=except_msg), "error")
        except sqlalchemy.exc.IntegrityError as except_msg:
            flash(gettext("Graph Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formModGraph)


def graph_del(formDelGraph, display_order):
    if deny_guest_user():
        return redirect('/graph')

    if formDelGraph.validate():
        try:
            delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                                 Graph,
                                 formDelGraph.graph_id.data)
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_graph = db_session.query(DisplayOrder).first()
                display_order.remove(formDelGraph.graph_id.data)
                order_graph.graph = ','.join(display_order)
                db_session.commit()
        except Exception as except_msg:
            flash(gettext("Graph Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formDelGraph)


def graph_reorder(formOrderGraph, display_order):
    if deny_guest_user():
        return redirect('/graph')

    if formOrderGraph.validate():
        try:
            if formOrderGraph.orderGraphUp.data:
                status, reord_list = reorderList(
                    display_order,
                    formOrderGraph.orderGraph_id.data,
                    'up')
            elif formOrderGraph.orderGraphDown.data:
                status, reord_list = reorderList(
                    display_order,
                    formOrderGraph.orderGraph_id.data,
                    'down')
            if status == 'success':
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_graph = db_session.query(DisplayOrder).first()
                    order_graph.graph = ','.join(reord_list)
                    db_session.commit()
            else:
                flash(reord_list, status)
        except Exception as except_msg:
            flash(gettext("Graph Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formOrderGraph)


#
# LCD Manipulation
#

def lcd_add(formAddLCD, display_order):
    if deny_guest_user():
        return redirect('/lcd')

    if formAddLCD.validate():
        for _ in range(0, formAddLCD.numberLCDs.data):
            new_lcd = LCD()
            random_lcd_id = ''.join([random.choice(
                    string.ascii_letters + string.digits) for _ in xrange(8)])
            new_lcd.id = random_lcd_id
            new_lcd.name = 'LCD {}'.format(random_lcd_id)
            new_lcd.pin = "27"
            new_lcd.multiplexer_address = ''
            new_lcd.multiplexer_channel = 0
            new_lcd.period = 30
            new_lcd.activated = 0
            new_lcd.x_characters = 16
            new_lcd.y_lines = 2
            new_lcd.line_1_sensor_id = ''
            new_lcd.line_1_measurement = ''
            new_lcd.line_2_sensor_id = ''
            new_lcd.line_2_measurement = ''
            new_lcd.line_3_sensor_id = ''
            new_lcd.line_3_measurement = ''
            new_lcd.line_4_sensor_id = ''
            new_lcd.line_4_measurement = ''
            try:
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    db_session.add(new_lcd)
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_lcd = db_session.query(DisplayOrder).first()
                    display_order.append(random_lcd_id)
                    order_lcd.lcd = ','.join(display_order)
                    db_session.commit()
            except sqlalchemy.exc.OperationalError as except_msg:
                flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
            except sqlalchemy.exc.IntegrityError as except_msg:
                flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddLCD)


def lcd_mod(formModLCD):
    if deny_guest_user():
        return redirect('/lcd')

    if formModLCD.validate():
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_lcd = db_session.query(LCD).filter(
                    LCD.id == formModLCD.modLCD_id.data).first()
                if mod_lcd.activated:
                    flash(gettext("Deactivate LCD controller before modifying"
                                  " its settings."), "error")
                    return redirect('/lcd')
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_lcd = db_session.query(LCD).filter(
                    LCD.id == formModLCD.modLCD_id.data).first()
                mod_lcd.name = formModLCD.modName.data
                mod_lcd.pin = formModLCD.modPin.data
                mod_lcd.multiplexer_address = formModLCD.modMultiplexAddress.data
                mod_lcd.multiplexer_channel = formModLCD.modMultiplexChannel.data
                mod_lcd.period = formModLCD.modPeriod.data
                mod_lcd.x_characters = formModLCD.modLCDType.data.split("x")[0]
                mod_lcd.y_lines = formModLCD.modLCDType.data.split("x")[1]
                if formModLCD.modLine1SensorIDMeasurement.data:
                    mod_lcd.line_1_sensor_id = formModLCD.modLine1SensorIDMeasurement.data.split(",")[0]
                    mod_lcd.line_1_measurement = formModLCD.modLine1SensorIDMeasurement.data.split(",")[1]
                else:
                    mod_lcd.line_1_sensor_id = ''
                    mod_lcd.line_1_measurement = ''
                if formModLCD.modLine2SensorIDMeasurement.data:
                    mod_lcd.line_2_sensor_id = formModLCD.modLine2SensorIDMeasurement.data.split(",")[0]
                    mod_lcd.line_2_measurement = formModLCD.modLine2SensorIDMeasurement.data.split(",")[1]
                else:
                    mod_lcd.line_2_sensor_id = ''
                    mod_lcd.line_2_measurement = ''
                if formModLCD.modLine3SensorIDMeasurement.data:
                    mod_lcd.line_3_sensor_id = formModLCD.modLine3SensorIDMeasurement.data.split(",")[0]
                    mod_lcd.line_3_measurement = formModLCD.modLine3SensorIDMeasurement.data.split(",")[1]
                else:
                    mod_lcd.line_3_sensor_id = ''
                    mod_lcd.line_3_measurement = ''
                if formModLCD.modLine4SensorIDMeasurement.data:
                    mod_lcd.line_4_sensor_id = formModLCD.modLine4SensorIDMeasurement.data.split(",")[0]
                    mod_lcd.line_4_measurement = formModLCD.modLine4SensorIDMeasurement.data.split(",")[1]
                else:
                    mod_lcd.line_4_sensor_id = ''
                    mod_lcd.line_4_measurement = ''
                db_session.commit()
        except Exception as except_msg:
            flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formModLCD)


def lcd_del(formDelLCD, display_order):
    if deny_guest_user():
        return redirect('/lcd')

    if formDelLCD.validate():
        try:
            delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                                 LCD,
                                 formDelLCD.delLCD_id.data)
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_lcd = db_session.query(DisplayOrder).first()
                display_order.remove(formDelLCD.delLCD_id.data)
                order_lcd.lcd = ','.join(display_order)
                db_session.commit()
        except Exception as except_msg:
            flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formDelLCD)


def lcd_reorder(formOrderLCD, display_order):
    if deny_guest_user():
        return redirect('/lcd')

    if formOrderLCD.validate():
        try:
            if formOrderLCD.orderLCDUp.data:
                status, reord_list = reorderList(
                    display_order,
                    formOrderLCD.orderLCD_id.data,
                    'up')
            elif formOrderLCD.orderLCDDown.data:
                status, reord_list = reorderList(
                    display_order,
                    formOrderLCD.orderLCD_id.data,
                    'down')
            if status == 'success':
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_lcd = db_session.query(DisplayOrder).first()
                    order_lcd.lcd = ','.join(reord_list)
                    db_session.commit()
            else:
                flash(reord_list, status)
        except Exception as except_msg:
            flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formOrderLCD)


def lcd_activate(formActivateLCD):
    if formActivateLCD.validate():
        try:
            # All sensors the LCD depends on must be active to activate the LCD
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                lcd = db_session.query(LCD).filter(
                    LCD.id == formActivateLCD.activateLCD_id.data).first()
                if lcd.y_lines == 2:
                    LCD_lines = [lcd.line_1_sensor_id,
                                  lcd.line_2_sensor_id]
                else:
                    LCD_lines = [lcd.line_1_sensor_id,
                                  lcd.line_2_sensor_id,
                                  lcd.line_3_sensor_id,
                                  lcd.line_4_sensor_id]
                # Filter only sensors that will be displayed
                sensor = db_session.query(Sensor).filter(
                    Sensor.id.in_(LCD_lines)).all()
                # Check if any sensors are not active
                for each_sensor in sensor:
                    if not each_sensor.is_activated():
                        flash(gettext("Cannot activate controller if the "
                                      "associated sensor controller is inactive"),
                              "error")
                        return redirect('/lcd')
            activate_deactivate_controller('activate',
                                           'LCD',
                                           formActivateLCD.activateLCD_id.data)
        except Exception as except_msg:
            flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formActivateLCD)


def lcd_deactivate(formDeactivateLCD):
    if formDeactivateLCD.validate():
        activate_deactivate_controller('deactivate',
                                       'LCD',
                                       formDeactivateLCD.deactivateLCD_id.data)
    else:
        flash_form_errors(formDeactivateLCD)


def lcd_reset_flashing(formResetFlashingLCD):
    if deny_guest_user():
        return redirect('/lcd')

    if formResetFlashingLCD.validate():
        control = DaemonControl()
        return_value, return_msg = control.flash_lcd(formResetFlashingLCD.flashLCD_id.data, 0)
        if not return_value:
            flash(gettext("LCD Error: %(err)s", err=return_msg), "error")
    else:
        flash_form_errors(formResetFlashingLCD)


#
# Logs
#


def log_add(formAddLog, display_order):
    if deny_guest_user():
        return redirect('/log')

    if formAddLog.validate():
        if formAddLog.period.data <= 0:
            flash(gettext("Error in the period field: Durations must be "
                          "greater than 0"), "error")
            return 1
        new_log = Log()
        random_log_id = ''.join([random.choice(
                string.ascii_letters + string.digits) for n in xrange(8)])
        new_log.id = random_log_id
        new_log.name = formAddLog.name.data
        new_log.activated = 0
        sensor_and_measurement_split = formAddLog.sensorMeasurement.data.split(",")
        new_log.sensor_id = sensor_and_measurement_split[0]
        new_log.measure_type = sensor_and_measurement_split[1]
        new_log.period = formAddLog.period.data
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                db_session.add(new_log)
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_log = db_session.query(DisplayOrder).first()
                    display_order.append(random_log_id)
                    order_log.log = ','.join(display_order)
                    db_session.commit()
        except sqlalchemy.exc.OperationalError as except_msg:
            flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
        except sqlalchemy.exc.IntegrityError as except_msg:
            flash(gettext("LCD Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddLog)


def log_mod(formLog):
    if deny_guest_user():
        return redirect('/log')

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            mod_log = db_session.query(Log).filter(
                Log.id == formLog.log_id.data).first()
            if mod_log.activated:
                flash(gettext("Deactivate log controller before modifying its"
                              " settings"), "error")
                return redirect('/log')
            mod_log.name = formLog.name.data
            sensor_and_measurement_split = formLog.sensorMeasurement.data.split(",")
            mod_log.sensor_id = sensor_and_measurement_split[0]
            mod_log.measure_type = sensor_and_measurement_split[1]
            mod_log.period = formLog.period.data
            db_session.commit()
    except Exception as except_msg:
        flash(gettext("LCD Error: %(err)s", err=except_msg), "error")


def log_del(formLog, display_order):
    if deny_guest_user():
        return redirect('/log')

    try:
        delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                             Log,
                             formLog.log_id.data)
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_sensor = db_session.query(DisplayOrder).first()
                display_order.remove(formLog.log_id.data)
                order_sensor.log = ','.join(display_order)
                db_session.commit()
    except Exception as except_msg:
        flash(gettext("LCD Error: %(err)s", err=except_msg), "error")


def log_reorder(formLog, display_order):
    if deny_guest_user():
        return redirect('/log')

    try:
        if formLog.orderLogUp.data:
            status, reord_list = reorderList(display_order,
                                             formLog.log_id.data,
                                             'up')
        elif formLog.orderLogDown.data:
            status, reord_list = reorderList(display_order,
                                             formLog.log_id.data,
                                             'down')
        if status == 'success':
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_log = db_session.query(DisplayOrder).first()
                order_log.log = ','.join(reord_list)
                db_session.commit()
        else:
            flash(reord_list, status)
    except Exception as except_msg:
        flash(gettext("LCD Error: %(err)s", err=except_msg), "error")


def log_activate(formLog):
    # Check if associated sensor is activated
    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        log = db_session.query(Log).filter(
            Log.id == formLog.log_id.data).first()
        sensor = db_session.query(Sensor).filter(
            Sensor.id == log.sensor_id).first()
        if not sensor.is_activated():
            flash(gettext("Cannot activate controller if the associated "
                          "sensor controller is inactive"), "error")
            return redirect('/log')
    activate_deactivate_controller('activate', 'Log', formLog.log_id.data)


def log_deactivate(formLog):
    activate_deactivate_controller('deactivate', 'Log', formLog.log_id.data)


#
# PID manipulation
#

def pid_add(formAddPID, display_order):
    if formAddPID.validate():
        for _ in range(0, formAddPID.numberPIDs.data):
            new_pid = PID()
            random_pid_id = ''.join([random.choice(
                    string.ascii_letters + string.digits) for _ in xrange(8)])
            new_pid.id = random_pid_id
            new_pid.name = 'PID {}'.format(random_pid_id)
            new_pid.activated = 0
            new_pid.sensor_id = ''
            new_pid.measure_type = ''
            new_pid.direction = 'raise'
            new_pid.period = 60
            new_pid.setpoint = 0.0
            new_pid.method_id = ''
            new_pid.p = 1.0
            new_pid.i = 0.0
            new_pid.d = 0.0
            new_pid.integrator_min = -100.0
            new_pid.integrator_max = 100.0
            new_pid.raise_relay_id = ''
            new_pid.raise_min_duration = 0
            new_pid.raise_max_duration = 0
            new_pid.lower_relay_id = ''
            new_pid.lower_min_duration = 0
            new_pid.lower_max_duration = 0
            try:
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    db_session.add(new_pid)
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_pid = db_session.query(DisplayOrder).first()
                    display_order.append(random_pid_id)
                    order_pid.pid = ','.join(display_order)
                    db_session.commit()
            except sqlalchemy.exc.OperationalError as except_msg:
                flash(gettext("PID Error: %(err)s", err=except_msg), "error")
            except sqlalchemy.exc.IntegrityError as except_msg:
                flash(gettext("PID Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddPID)


def pid_mod(formModPID):
    if formModPID.validate():
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                error = False
                sensor = db_session.query(Sensor).filter(
                    Sensor.id == formModPID.modSensorID.data).first()
                if not sensor:
                    flash(gettext("A valid sensor ID is required"),
                          "error")
                    error = True
                elif (
                      (sensor.device_type == 'tsensor' and
                       formModPID.modMeasureType.data not in ['temperature']) or

                      (sensor.device_type == 'tmpsensor' and
                       formModPID.modMeasureType.data not in ['temperature_object',
                                                              'temperature_die']) or

                      (sensor.device_type == 'htsensor' and
                       formModPID.modMeasureType.data not in ['temperature',
                                                              'humidity',
                                                              'dewpoint']) or

                      (sensor.device_type == 'co2sensor' and
                       formModPID.modMeasureType.data not in ['co2']) or

                      (sensor.device_type == 'luxsensor' and
                       formModPID.modMeasureType.data not in ['lux']) or

                      (sensor.device_type == 'moistsensor' and
                       formModPID.modMeasureType.data not in ['temperature',
                                                              'lux',
                                                              'moisture']) or

                      (sensor.device_type == 'presssensor' and
                       formModPID.modMeasureType.data not in ['temperature',
                                                              'pressure',
                                                              'altitude'])
                ):
                    flash(gettext("Select a Measure Type that is compatible "
                                  "with the chosen sensor"), "error")
                    error = True
                if error:
                    return redirect('/pid')

                mod_pid = db_session.query(PID).filter(
                    PID.id == formModPID.modPID_id.data).first()
                mod_pid.name = formModPID.modName.data
                mod_pid.sensor_id = formModPID.modSensorID.data
                mod_pid.measure_type = formModPID.modMeasureType.data
                mod_pid.direction = formModPID.modDirection.data
                mod_pid.period = formModPID.modPeriod.data
                mod_pid.setpoint = formModPID.modSetpoint.data
                mod_pid.p = formModPID.modKp.data
                mod_pid.i = formModPID.modKi.data
                mod_pid.d = formModPID.modKd.data
                mod_pid.integrator_min = formModPID.modIntegratorMin.data
                mod_pid.integrator_max = formModPID.modIntegratorMax.data
                mod_pid.raise_relay_id = formModPID.modRaiseRelayID.data
                mod_pid.raise_min_duration = formModPID.modRaiseMinDuration.data
                mod_pid.raise_max_duration = formModPID.modRaiseMaxDuration.data
                mod_pid.lower_relay_id = formModPID.modLowerRelayID.data
                mod_pid.lower_min_duration = formModPID.modLowerMinDuration.data
                mod_pid.lower_max_duration = formModPID.modLowerMaxDuration.data
                mod_pid.method_id = formModPID.mod_method_id.data
                db_session.commit()
                # If the controller is active or paused, refresh variables in thread
                if mod_pid.activated:
                    control = DaemonControl()
                    return_value = control.pid_mod(formModPID.modPID_id.data)
                    flash(gettext("PID Controller settings refresh response: "
                                  "%(resp)s", resp=return_value), "success")
        except Exception as except_msg:
            flash(gettext("PID Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formModPID)


def pid_del(pid_id, display_order):
    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            pid = db_session.query(PID).filter(
                PID.id == pid_id).first()
            if pid.activated:
                pid_deactivate(pid_id)

        delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                             PID,
                             pid_id)
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            order_pid = db_session.query(DisplayOrder).first()
            display_order.remove(pid_id)
            order_pid.pid = ','.join(display_order)
            db_session.commit()
    except Exception as except_msg:
        flash(gettext("PID Error: %(err)s", err=except_msg), "error")


def pid_reorder(pid_id, display_order, direction):
    try:
        if direction == 'up':
            status, reord_list = reorderList(display_order, pid_id, 'up')
        elif direction == 'down':
            status, reord_list = reorderList(display_order, pid_id, 'down')
        if status == 'success':
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_pid = db_session.query(DisplayOrder).first()
                order_pid.pid = ','.join(reord_list)
                db_session.commit()
        else:
            flash(reord_list, status)
    except Exception as except_msg:
        flash(gettext("PID Error: %(err)s", err=except_msg), "error")


def has_required_pid_values(pid_id):
    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        pid = db_session.query(PID).filter(
            PID.id == pid_id).first()
        error = False
        # TODO: Add more settings-checks before allowing controller to be activated
        if not pid.sensor_id:
            flash(gettext("A valid sensor  is required"), "error")
            error = True
        if not pid.measure_type:
            flash(gettext("A valid Measure Type is required"), "error")
            error = True
        if not pid.raise_relay_id and not pid.lower_relay_id:
            flash(gettext("A Raise Relay ID and/or a Lower Relay ID is "
                          "required"), "error")
            error = True
        if error:
            return redirect('/pid')


def pid_activate(pid_id):
    if has_required_pid_values(pid_id):
        return redirect('/pid')

    # Check if associated sensor is activated
    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        pid = db_session.query(PID).filter(
            PID.id == pid_id).first()
        sensor = db_session.query(Sensor).filter(
            Sensor.id == pid.sensor_id).first()
        if not sensor.is_activated():
            flash(gettext("Cannot activate controller if the associated "
                          "sensor controller is inactive"), "error")
            return redirect('/pid')

        # Signal the duration method can run because it's been
        # properly initiated (non-power failure)
        mod_method = db_session.query(Method).filter(
            Method.method_id == pid.method_id)
        mod_method = mod_method.filter(Method.method_order == 0).first()
        if mod_method and mod_method.method_type == 'Duration':
            mod_method.start_time = 'Ready'
            db_session.commit()
    time.sleep(1)
    activate_deactivate_controller('activate',
                                   'PID',
                                   pid_id)


def pid_deactivate(pid_id):
    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        pid = db_session.query(PID).filter(
            PID.id == pid_id).first()
        pid.activated = 0
        db_session.commit()
    time.sleep(1)
    activate_deactivate_controller('deactivate',
                                   'PID',
                                   pid_id)


def pid_manipulate(pid_id, action):
    if action not in ['Hold', 'Pause', 'Resume']:
        flash(gettext("Invalid PID action: %(act)s", act=action), "error")
        return 1

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            mod_pid = db_session.query(PID).filter(
                PID.id == pid_id).first()
            if action == 'Hold':
                mod_pid.activated = 3
            elif action == 'Pause':
                mod_pid.activated = 2
            elif action == 'Resume':
                mod_pid.activated = 1
            db_session.commit()

        control = DaemonControl()
        if action == 'Hold':
            return_value = control.pid_hold(pid_id)
        elif action == 'Pause':
            return_value = control.pid_pause(pid_id)
        elif action == 'Resume':
            return_value = control.pid_resume(pid_id)
        flash(gettext("Daemon response to PID controller %(act)s command: "
                      "%(rval)s", act=action, rval=return_value), "success")
    except Exception as err:
        flash(gettext("PID Error: %(err)s", err=err), "error")


#
# Relay manipulation
#

def relay_on_off(formRelayOnOff):
    if deny_guest_user():
        return redirect('/relay')

    try:
        control = DaemonControl()
        if int(formRelayOnOff.Relay_pin.data) <= 0:
            flash(gettext("Cannot modulate relay with a GPIO of 0"), "error")
        elif formRelayOnOff.On.data:
            return_value = control.relay_on(formRelayOnOff.Relay_id.data, 0)
            flash(gettext("Relay successfully turned on: %(rvalue)s",
                          rvalue=return_value), "success")
        elif formRelayOnOff.Off.data:
            return_value = control.relay_off(formRelayOnOff.Relay_id.data)
            flash(gettext("Relay successfully turned off: %(rvalue)s",
                          rvalue=return_value), "success")
    except Exception as except_msg:
        flash(gettext("Relay Error: %(err)s", err=except_msg), "error")


def relay_add(formAddRelay, display_order):
    if deny_guest_user():
        return redirect('/relay')

    if formAddRelay.validate():
        for _ in range(0, formAddRelay.numberRelays.data):
            new_relay = Relay()
            random_relay_id = ''.join([random.choice(
                    string.ascii_letters + string.digits) for n in xrange(8)])
            new_relay.id = random_relay_id
            new_relay.name = 'Relay'
            new_relay.pin = 0
            new_relay.amps = 0
            new_relay.trigger = 1
            new_relay.start_state = 0
            try:
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    db_session.add(new_relay)
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_relay = db_session.query(DisplayOrder).first()
                    display_order.append(random_relay_id)
                    order_relay.relay = ','.join(display_order)
                    db_session.commit()
                manipulate_relay(random_relay_id, 'add')
            except sqlalchemy.exc.OperationalError as except_msg:
                flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
            except sqlalchemy.exc.IntegrityError as except_msg:
                flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddRelay)


def relay_mod(formModRelay):
    if deny_guest_user():
        return redirect('/relay')

    if formModRelay.validate():
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_relay = db_session.query(Relay).filter(
                    Relay.id == formModRelay.modRelay_id.data).first()
                mod_relay.name = formModRelay.modName.data
                mod_relay.pin = formModRelay.modGpio.data
                mod_relay.amps = formModRelay.modAmps.data
                mod_relay.trigger = formModRelay.modTrigger.data
                mod_relay.start_state = formModRelay.modStartState.data
                db_session.commit()
                manipulate_relay(formModRelay.modRelay_id.data, 'mod')
        except Exception as except_msg:
            flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formModRelay)


def relay_del(formDelRelay, display_order):
    if deny_guest_user():
        return redirect('/relay')

    if formDelRelay.validate():
        try:
            delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                                 Relay,
                                 formDelRelay.delRelay_id.data)
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_relay = db_session.query(DisplayOrder).first()
                display_order.remove(formDelRelay.delRelay_id.data)
                order_relay.relay = ','.join(display_order)
                db_session.commit()
            manipulate_relay(formDelRelay.delRelay_id.data, 'del')
        except Exception as except_msg:
            flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formDelRelay)


def relay_reorder(formOrderRelay, display_order):
    if deny_guest_user():
        return redirect('/relay')

    if formOrderRelay.validate():
        try:
            if formOrderRelay.orderRelayUp.data:
                status, reord_list = reorderList(
                    display_order,
                    formOrderRelay.orderRelay_id.data,
                    'up')
            elif formOrderRelay.orderRelayDown.data:
                status, reord_list = reorderList(
                    display_order,
                    formOrderRelay.orderRelay_id.data,
                    'down')
            if status == 'success':
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_relay = db_session.query(DisplayOrder).first()
                    order_relay.relay = ','.join(reord_list)
                    db_session.commit()
            else:
                flash(reord_list, status)
        except Exception as except_msg:
            flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formOrderRelay)


#
# Relay conditional manipulation
#

def relay_conditional_add(formAddRelayCond):
    if deny_guest_user():
        return redirect('/relay')

    if formAddRelayCond.validate():
        for _ in range(0, formAddRelayCond.numberRelayConditionals.data):
            new_relay_cond = RelayConditional()
            random_id = ''.join([random.choice(
                    string.ascii_letters + string.digits) for _ in xrange(8)])
            new_relay_cond.id = random_id
            new_relay_cond.name = 'Relay Conditional'
            new_relay_cond.activated = False
            new_relay_cond.if_relay_id = ''
            new_relay_cond.if_action = ''
            new_relay_cond.if_duration = 0
            new_relay_cond.do_relay_id = ''
            new_relay_cond.do_action = ''
            new_relay_cond.do_duration = 0
            new_relay_cond.execute_command = ''
            new_relay_cond.email_notify = ''
            new_relay_cond.flash_lcd = ''
            try:
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    db_session.add(new_relay_cond)
            except sqlalchemy.exc.OperationalError as except_msg:
                flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
            except sqlalchemy.exc.IntegrityError as except_msg:
                flash(gettext("Relay Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddRelayCond)


def relay_conditional_mod(formModRelayCond):
    if deny_guest_user():
        return redirect('/relay')

    try:
        if formModRelayCond.activate.data:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                relay_cond = db_session.query(RelayConditional)
                relay_cond = relay_cond.filter(
                    RelayConditional.id == formModRelayCond.Relay_id.data).first()
                relay_cond.activated = True
                db_session.commit()
        elif formModRelayCond.deactivate.data:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                relay_cond = db_session.query(RelayConditional).filter(
                    RelayConditional.id == formModRelayCond.Relay_id.data).first()
                relay_cond.activated = 0
                db_session.commit()
        elif formModRelayCond.delCondRelaySubmit.data:
            delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                                 RelayConditional,
                                 formModRelayCond.Relay_id.data)
        elif (formModRelayCond.modCondRelaySubmit.data and
                formModRelayCond.validate()):
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_relay = db_session.query(RelayConditional).filter(
                    RelayConditional.id == formModRelayCond.Relay_id.data).first()
                mod_relay.name = formModRelayCond.modCondName.data
                mod_relay.if_relay_id = formModRelayCond.IfRelayID.data
                mod_relay.if_action = formModRelayCond.IfRelayAction.data
                mod_relay.if_duration = formModRelayCond.IfRelayDuration.data
                mod_relay.do_relay_id = formModRelayCond.DoRelayID.data
                mod_relay.do_action = formModRelayCond.DoRelayAction.data
                mod_relay.do_duration = formModRelayCond.DoRelayDuration.data
                mod_relay.execute_command = formModRelayCond.DoExecute.data
                mod_relay.email_notify = formModRelayCond.DoNotify.data
                mod_relay.flash_lcd = formModRelayCond.DoFlashLCD.data
                db_session.commit()
        else:
            flash_form_errors(formModRelayCond)
    except Exception as except_msg:
        flash(gettext("Relay Error: %(err)s", err=except_msg), "error")


def sum_relay_usage(relay_id, past_seconds):
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER,
                            INFLUXDB_PASSWORD, INFLUXDB_DATABASE)
    if relay_id:
        query = """SELECT sum(value)
                       FROM   duration_sec
                       WHERE  device_id = '{}'
                              AND TIME > Now() - {}s;
                """.format(relay_id, past_seconds)
    else:
        query = """SELECT sum(value)
                       FROM   duration_sec
                       WHERE  TIME > Now() - {}s;
                """.format(past_seconds)
    output = client.query(query)
    if output:
        return output.raw['series'][0]['values'][0][1]
    else:
        return 0


#
# Sensor manipulation
#

def sensor_add(formAddSensor, display_order):
    if deny_guest_user():
        return redirect('/sensor')

    if formAddSensor.validate():
        for _ in range(0, formAddSensor.numberSensors.data):
            new_sensor = Sensor()
            random_sensor_id = ''.join([random.choice(
                    string.ascii_letters + string.digits) for _ in xrange(8)])
            new_sensor.id = random_sensor_id
            new_sensor.device = formAddSensor.sensor.data
            new_sensor.name = '{} ({})'.format(formAddSensor.sensor.data,
                                               random_sensor_id)
            if GPIO.RPI_INFO['P1_REVISION'] in [2, 3]:
                new_sensor.i2c_bus = 1
                new_sensor.multiplexer_bus = 1
            else:
                new_sensor.i2c_bus = 0
                new_sensor.multiplexer_bus = 0
            new_sensor.location = ''
            new_sensor.multiplexer_address = ''
            new_sensor.multiplexer_channel = 0
            new_sensor.adc_channel = 0
            new_sensor.adc_gain = 1
            new_sensor.adc_resolution = 18
            new_sensor.adc_measure = 'Condition'
            new_sensor.adc_measure_units = 'Unit'
            new_sensor.adc_units_min = 0.0
            new_sensor.adc_units_max = 10.0
            new_sensor.switch_edge = 'rising'
            new_sensor.switch_bouncetime = 50
            new_sensor.switch_reset_period = 10
            new_sensor.pre_relay_duration = 0
            new_sensor.activated = 0
            new_sensor.graph = 0
            new_sensor.period = 15
            new_sensor.sht_clock_pin = 0
            new_sensor.sht_voltage = 3.5

            # Process monitors
            if formAddSensor.sensor.data in ['RPiCPULoad']:
                new_sensor.device_type = 'cpu_load'
                new_sensor.location = 'RPi'
            elif formAddSensor.sensor.data == 'EDGE':
                new_sensor.device_type = 'edgedetect'

            # Environmental Sensors
            # Temperature
            if formAddSensor.sensor.data in ['ATLAS_PT1000', 'DS18B20', 'RPi', 'TMP006']:
                new_sensor.device_type = 'tsensor'
                if formAddSensor.sensor.data == 'ATLAS_PT1000':
                    new_sensor.location = '0x66'
                elif formAddSensor.sensor.data == 'RPi':
                    new_sensor.location = 'RPi'
                elif formAddSensor.sensor.data == 'TMP006':
                    new_sensor.location = '0x40'
            
            # Temperature/Humidity
            elif formAddSensor.sensor.data in ['AM2315', 'DHT11', 'DHT22',
                                               'HTU21D', 'SHT1x_7x', 'SHT2x']:
                new_sensor.device_type = 'htsensor'
                if formAddSensor.sensor.data == 'AM2315':
                    new_sensor.location = '0x5c'
                elif formAddSensor.sensor.data == 'HTU21D':
                    new_sensor.location = '0x40'
                elif formAddSensor.sensor.data == 'SHT2x':
                    new_sensor.location = '0x40'

            # Chirp moisture sensor
            elif formAddSensor.sensor.data == 'CHIRP':
                new_sensor.device_type = 'moistsensor'
                new_sensor.location = '0x20'

            # CO2
            elif formAddSensor.sensor.data == 'K30':
                new_sensor.device_type = 'co2sensor'
                new_sensor.location = 'Tx/Rx'
            
            # Pressure
            elif formAddSensor.sensor.data in ['BME280', 'BMP']:
                new_sensor.device_type = 'presssensor'
                if formAddSensor.sensor.data == 'BME280':
                    new_sensor.location = '0x76'
                elif formAddSensor.sensor.data == 'BMP':
                    new_sensor.location = '0x77'

            # Light
            elif formAddSensor.sensor.data == 'TSL2561':
                new_sensor.device_type = 'luxsensor'
                new_sensor.location = '0x39'

            # Analog to Digital Converters
            elif formAddSensor.sensor.data in ['ADS1x15', 'MCP342x']:
                new_sensor.device_type = 'analogsensor'
                if formAddSensor.sensor.data == 'ADS1x15':
                    new_sensor.location = '0x48'
                    new_sensor.adc_volts_min = -4.096
                    new_sensor.adc_volts_max = 4.096
                elif formAddSensor.sensor.data == 'MCP342x':
                    new_sensor.location = '0x68'
                    new_sensor.adc_volts_min = -2.048
                    new_sensor.adc_volts_max = 2.048

            try:
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    db_session.add(new_sensor)
                with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_sensor = db_session.query(DisplayOrder).first()
                    display_order.append(random_sensor_id)
                    order_sensor.sensor = ','.join(display_order)
                    db_session.commit()
            except sqlalchemy.exc.OperationalError as except_msg:
                flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
            except sqlalchemy.exc.IntegrityError as except_msg:
                flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddSensor)


def sensor_mod(formModSensor):
    if deny_guest_user():
        return redirect('/sensor')

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            mod_sensor = db_session.query(Sensor).filter(
                Sensor.id == formModSensor.modSensor_id.data).first()

            error = False

            if not formModSensor.modLocation.data:
                flash(gettext("Invalid device GPIO/I2C address/location"),
                      "error")
                error = True
            if mod_sensor.activated:
                flash(gettext("Deactivate sensor controller before modifying "
                              "its settings"), "error")
                error = True
            if (mod_sensor.device == 'AM2315' and
                    formModSensor.modPeriod.data < 7):
                flash(gettext("Choose a Read Period equal to or greater than "
                              "7. The AM2315 may become unresponsive if the "
                              "period is below 7."), "error")
                error = True
            if formModSensor.modPeriod.data < mod_sensor.pre_relay_duration:
                flash(gettext("The Read Period cannot be less than the "
                              "Pre-Relay Duration"), "error")
                error = True

            if error:
                return redirect('/sensor')

            mod_sensor.name = formModSensor.modName.data
            mod_sensor.i2c_bus = formModSensor.modBus.data
            mod_sensor.location = formModSensor.modLocation.data
            mod_sensor.multiplexer_address = formModSensor.modMultiplexAddress.data
            mod_sensor.multiplexer_bus = formModSensor.modMultiplexBus.data
            mod_sensor.multiplexer_channel = formModSensor.modMultiplexChannel.data
            mod_sensor.adc_channel = formModSensor.modADCChannel.data
            mod_sensor.adc_gain = formModSensor.modADCGain.data
            mod_sensor.adc_resolution = formModSensor.modADCResolution.data
            mod_sensor.adc_measure = formModSensor.modADCMeasure.data.replace(" ", "_")
            mod_sensor.adc_measure_units = formModSensor.modADCMeasureUnits.data
            mod_sensor.adc_volts_min = formModSensor.modADCVoltsMin.data
            mod_sensor.adc_volts_max = formModSensor.modADCVoltsMax.data
            mod_sensor.adc_units_min = formModSensor.modADCUnitsMin.data
            mod_sensor.adc_units_max = formModSensor.modADCUnitsMax.data
            mod_sensor.switch_edge = formModSensor.modSwitchEdge.data
            mod_sensor.switch_bouncetime = formModSensor.modSwitchBounceTime.data
            mod_sensor.switch_reset_period = formModSensor.modSwitchResetPeriod.data
            mod_sensor.pre_relay_id = formModSensor.modPreRelayID.data
            mod_sensor.pre_relay_duration = formModSensor.modPreRelayDuration.data
            mod_sensor.period = formModSensor.modPeriod.data
            mod_sensor.sht_clock_pin = formModSensor.modSHTClockPin.data
            mod_sensor.sht_voltage = float(formModSensor.modSHTVoltage.data)
            db_session.commit()
    except Exception as except_msg:
        flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")


def sensor_del(formModSensor, display_order):
    if deny_guest_user():
        return redirect('/sensor')

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            sensor = db_session.query(Sensor).filter(
                Sensor.id == formModSensor.modSensor_id.data).first()
            if sensor.activated:
                sensor_deactivate_associated_controllers(
                    formModSensor.modSensor_id.data)
                activate_deactivate_controller(
                    'deactivate', 'Sensor',
                    formModSensor.modSensor_id.data)

            sensor_cond = db_session.query(SensorConditional).all()
            for each_sensor_cond in sensor_cond:
                if each_sensor_cond.sensor_id == formModSensor.modSensor_id.data:
                    delete_entry_with_id(
                        current_app.config['MYCODO_DB_PATH'],
                        SensorConditional,
                        each_sensor_cond.id)

        delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                             Sensor,
                             formModSensor.modSensor_id.data)
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            order_sensor = db_session.query(DisplayOrder).first()
            display_order.remove(formModSensor.modSensor_id.data)
            order_sensor.sensor = ','.join(display_order)
            db_session.commit()
    except Exception as except_msg:
        flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")


def sensor_reorder(formModSensor, display_order):
    if deny_guest_user():
        return redirect('/sensor')

    try:
        if formModSensor.orderSensorUp.data:
            status, reord_list = reorderList(display_order,
                                             formModSensor.modSensor_id.data,
                                             'up')
        elif formModSensor.orderSensorDown.data:
            status, reord_list = reorderList(display_order,
                                             formModSensor.modSensor_id.data,
                                             'down')
        if status == 'success':
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_sensor = db_session.query(DisplayOrder).first()
                order_sensor.sensor = ','.join(reord_list)
                db_session.commit()
        else:
            flash(reord_list, status)
    except Exception as except_msg:
        flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")


def sensor_activate(formModSensor):
    if deny_guest_user():
        return redirect('/sensor')

    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        sensor = db_session.query(Sensor).filter(
            Sensor.id == formModSensor.modSensor_id.data).first()
        if not sensor.location:
            flash("Cannot activate sensor without the GPIO/I2C Address/Port "
                  "to communicate with it set.", "error")
            return redirect('/sensor')
    activate_deactivate_controller('activate',
                                   'Sensor',
                                   formModSensor.modSensor_id.data)


def sensor_deactivate(formModSensor):
    if deny_guest_user():
        return redirect('/sensor')

    sensor_deactivate_associated_controllers(
        formModSensor.modSensor_id.data)
    activate_deactivate_controller('deactivate',
                                   'Sensor',
                                   formModSensor.modSensor_id.data)


# Deactivate any active PID or LCD controllers using this sensor
def sensor_deactivate_associated_controllers(sensor_id):
    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        pid = db_session.query(PID).filter(and_(
                PID.sensor_id == sensor_id,
                PID.activated == 1)).all()
        if pid:
            for each_pid in pid:
                activate_deactivate_controller('deactivate',
                                               'PID',
                                               each_pid.id)
        lcd = db_session.query(LCD).filter(LCD.activated)
        for each_lcd in lcd:
            if sensor_id in [each_lcd.line_1_sensor_id,
                             each_lcd.line_2_sensor_id,
                             each_lcd.line_3_sensor_id,
                             each_lcd.line_4_sensor_id]:
                activate_deactivate_controller('deactivate',
                                               'LCD',
                                               each_lcd.id)


#
# Sensor conditional manipulation
#

def sensor_conditional_add(formModSensor):
    if deny_guest_user():
        return redirect('/sensor')

    new_sensor_cond = SensorConditional()
    random_id = ''.join([random.choice(
            string.ascii_letters + string.digits) for n in xrange(8)])
    new_sensor_cond.id = random_id
    new_sensor_cond.name = 'Sensor Conditional'
    new_sensor_cond.activated = 0
    new_sensor_cond.sensor_id = formModSensor.modSensor_id.data
    new_sensor_cond.period = 60
    new_sensor_cond.measurement_type = ''
    new_sensor_cond.edge_select = 'edge'
    new_sensor_cond.edge_detected = 'rising'
    new_sensor_cond.gpio_state = 1
    new_sensor_cond.direction = ''
    new_sensor_cond.setpoint = 0.0
    new_sensor_cond.relay_id = ''
    new_sensor_cond.relay_state = ''
    new_sensor_cond.relay_on_duration = 0.0
    new_sensor_cond.execute_command = ''
    new_sensor_cond.email_notify = ''
    new_sensor_cond.flash_lcd = ''
    new_sensor_cond.camera_record = ''
    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            db_session.add(new_sensor_cond)
        check_refresh_conditional(formModSensor.modSensor_id.data,
                                  'add',
                                  random_id)
    except sqlalchemy.exc.OperationalError as except_msg:
        flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
    except sqlalchemy.exc.IntegrityError as except_msg:
        flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")


def sensor_conditional_mod(formModSensorCond):
    if deny_guest_user():
        return redirect('/sensor')

    if formModSensorCond.delSubmit.data:
        try:
            delete_entry_with_id(
                current_app.config['MYCODO_DB_PATH'],
                SensorConditional,
                formModSensorCond.modCondSensor_id.data)
            check_refresh_conditional(formModSensorCond.modSensor_id.data,
                                      'del',
                                      formModSensorCond.modCondSensor_id.data)
        except Exception as except_msg:
            flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
    elif (formModSensorCond.modSubmit.data and
            formModSensorCond.validate()):
        try:
            error = False
            if (formModSensorCond.DoRecord.data == 'photoemail' or formModSensorCond.DoRecord.data == 'videoemail') and not formModSensorCond.DoNotify.data:
                flash(gettext("A notification email address is required "
                              "if the record and email option is selected"),
                      "error")
                error = True
            if error:
                return redirect('/sensor')
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_sensor = db_session.query(SensorConditional).filter(
                    SensorConditional.id == formModSensorCond.modCondSensor_id.data).first()
                mod_sensor.name = formModSensorCond.modCondName.data
                mod_sensor.period = formModSensorCond.Period.data
                mod_sensor.measurement_type = formModSensorCond.MeasureType.data
                mod_sensor.edge_select = formModSensorCond.EdgeSelect.data
                mod_sensor.edge_detected = formModSensorCond.EdgeDetected.data
                mod_sensor.gpio_state = formModSensorCond.GPIOState.data
                mod_sensor.direction = formModSensorCond.Direction.data
                mod_sensor.setpoint = formModSensorCond.Setpoint.data
                mod_sensor.relay_id = formModSensorCond.modCondRelayID.data
                mod_sensor.relay_state = formModSensorCond.RelayState.data
                mod_sensor.relay_on_duration = formModSensorCond.RelayDuration.data
                mod_sensor.execute_command = formModSensorCond.DoExecute.data
                mod_sensor.email_notify = formModSensorCond.DoNotify.data
                mod_sensor.flash_lcd = formModSensorCond.DoFlashLCD.data
                mod_sensor.camera_record = formModSensorCond.DoRecord.data
                db_session.commit()
                check_refresh_conditional(formModSensorCond.modSensor_id.data,
                                          'mod',
                                          formModSensorCond.modCondSensor_id.data)
        except Exception as except_msg:
            flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
    elif formModSensorCond.activateSubmit.data:
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_sensor = db_session.query(SensorConditional).filter(
                    SensorConditional.id == formModSensorCond.modCondSensor_id.data).first()
                sensor = db_session.query(Sensor).filter(
                    Sensor.id == mod_sensor.sensor_id).first()

                device_specific_configured = False
                cond_configured = False
                
                # Ensure device-specific settings configured properly
                if sensor.device == 'EDGE' and mod_sensor.edge_detected:
                    device_specific_configured = True
                elif (sensor.device != 'EDGE' and
                        mod_sensor.period and
                        mod_sensor.measurement_type and
                        mod_sensor.direction):
                    device_specific_configured = True

                # Ensure universal conditional settings configured properly
                if ((mod_sensor.relay_id and mod_sensor.relay_state) or
                        mod_sensor.execute_command or
                        mod_sensor.email_notify or
                        mod_sensor.flash_lcd or
                        mod_sensor.camera_record):
                    cond_configured = True

                if device_specific_configured and cond_configured:
                    mod_sensor.activated = 1
                    db_session.commit()
                    check_refresh_conditional(formModSensorCond.modSensor_id.data,
                                              'mod',
                                              formModSensorCond.modCondSensor_id.data)
                else:
                    flash(gettext("Cannot activate sensor conditional "
                                  "%(cond)s because of an incomplete "
                                  "configuration",
                                  cond=formModSensorCond.modCondSensor_id.data),
                          "error")
                return redirect('/sensor')
        except Exception as except_msg:
            flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
    elif formModSensorCond.deactivateSubmit.data:
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_sensor = db_session.query(SensorConditional).filter(
                    SensorConditional.id == formModSensorCond.modCondSensor_id.data).first()
                mod_sensor.activated = 0
                db_session.commit()
                check_refresh_conditional(formModSensorCond.modSensor_id.data,
                                          'mod',
                                          formModSensorCond.modCondSensor_id.data)
        except Exception as except_msg:
            flash(gettext("Sensor Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formModSensorCond)


def check_refresh_conditional(sensor_id, cond_mod, cond_id):
    with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
        sensor = db_session.query(Sensor).filter(and_(
                Sensor.id == sensor_id,
                Sensor.activated == 1)).first()
        if sensor:
            control = DaemonControl()
            control.refresh_sensor_conditionals(sensor_id, cond_mod, cond_id)


#
# Timers
#

def timer_add(formAddTimer, timerType, display_order):
    if deny_guest_user():
        return redirect('/timer')
    
    if formAddTimer.validate():
        new_timer = Timer()
        random_timer_id = ''.join([random.choice(
                string.ascii_letters + string.digits) for n in xrange(8)])
        new_timer.id = random_timer_id
        new_timer.name = formAddTimer.name.data
        new_timer.activated = 0
        new_timer.relay_id = formAddTimer.relayID.data
        if timerType == 'time':
            new_timer.timer_type = 'time'
            new_timer.state = formAddTimer.state.data
            new_timer.time_start = formAddTimer.timeStart.data
            new_timer.duration_on = formAddTimer.timeOnDurationOn.data
            new_timer.duration_off = 0
        elif timerType == 'timespan':
            new_timer.timer_type = 'timespan'
            new_timer.state = formAddTimer.state.data
            new_timer.time_start = formAddTimer.timeStart.data
            new_timer.time_end = formAddTimer.timeEnd.data
        elif timerType == 'duration':
            if (formAddTimer.durationOn.data <= 0 or
                    formAddTimer.durationOff.data <= 0):
                flash(gettext("Error in the Duration field(s): Durations "
                              "must be greater than 0"), "error")
                return 1
            new_timer.timer_type = 'duration'
            new_timer.duration_on = formAddTimer.durationOn.data
            new_timer.duration_off = formAddTimer.durationOff.data
        try:
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                db_session.add(new_timer)
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                    order_timer = db_session.query(DisplayOrder).first()
                    display_order.append(random_timer_id)
                    order_timer.timer = ','.join(display_order)
                    db_session.commit()
        except sqlalchemy.exc.OperationalError as except_msg:
            flash(gettext("Timer Error: %(err)s", err=except_msg), "error")
        except sqlalchemy.exc.IntegrityError  as except_msg:
            flash(gettext("Timer Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddTimer)


def timer_mod(formTimer, timerType):
    if deny_guest_user():
        return redirect('/timer')

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
            mod_timer = db_session.query(Timer).filter(
                Timer.id == formTimer.timer_id.data).first()
            if mod_timer.activated:
                flash(gettext("Deactivate timer controller before modifying "
                              "its settings"), "error")
                return redirect('/timer')
            mod_timer.name = formTimer.name.data
            mod_timer.relay_id = formTimer.relayID.data
            if mod_timer.timer_type == 'time':
                mod_timer.state = formTimer.state.data
                mod_timer.time_start = formTimer.timeStart.data
                mod_timer.duration_on = formTimer.timeOnDurationOn.data
            elif mod_timer.timer_type == 'timespan':
                mod_timer.state = formTimer.state.data
                mod_timer.time_start = formTimer.timeStart.data
                mod_timer.time_end = formTimer.timeEnd.data
            elif mod_timer.timer_type == 'duration':
                mod_timer.duration_on = formTimer.durationOn.data
                mod_timer.duration_off = formTimer.durationOff.data
            db_session.commit()
    except Exception as except_msg:
        flash(gettext("Timer Error: %(err)s", err=except_msg), "error")


def timer_del(formTimer, display_order):
    if deny_guest_user():
        return redirect('/timer')

    try:
        delete_entry_with_id(current_app.config['MYCODO_DB_PATH'],
                             Timer,
                             formTimer.timer_id.data)
        with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_sensor = db_session.query(DisplayOrder).first()
                display_order.remove(formTimer.timer_id.data)
                order_sensor.timer = ','.join(display_order)
                db_session.commit()
    except Exception as except_msg:
        flash(gettext("Timer Error: %(err)s", err=except_msg), "error")


def timer_reorder(formTimer, display_order):
    if deny_guest_user():
        return redirect('/timer')

    try:
        if formTimer.orderTimerUp.data:
            status, reord_list = reorderList(display_order,
                                             formTimer.timer_id.data,
                                             'up')
        elif formTimer.orderTimerDown.data:
            status, reord_list = reorderList(display_order,
                                             formTimer.timer_id.data,
                                             'down')
        if status == 'success':
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                order_timer = db_session.query(DisplayOrder).first()
                order_timer.timer = ','.join(reord_list)
                db_session.commit()
        else:
            flash(reord_list, status)
    except Exception as except_msg:
        flash(gettext("Timer Error: %(err)s", err=except_msg), "error")


def timer_activate(formTimer):
    activate_deactivate_controller('activate',
                                   'Timer',
                                   formTimer.timer_id.data)


def timer_deactivate(formTimer):
    activate_deactivate_controller('deactivate',
                                   'Timer',
                                   formTimer.timer_id.data)


#
# User manipulation
#

def user_add(formAddUser):
    if deny_guest_user():
        return redirect('/')

    if formAddUser.validate():
        new_user = Users()
        if test_username(formAddUser.addUsername.data):
            new_user.user_name = formAddUser.addUsername.data
        new_user.user_email = formAddUser.addEmail.data
        if test_password(formAddUser.addPassword.data):
            new_user.set_password(formAddUser.addPassword.data)
        new_user.user_restriction = formAddUser.addGroup.data
        new_user.user_theme = 'slate'
        try:
            with session_scope(current_app.config['USER_DB_PATH']) as db_session:
                db_session.add(new_user)
        except sqlalchemy.exc.OperationalError as except_msg:
            flash(gettext("User Error: %(err)s", err=except_msg), "error")
        except sqlalchemy.exc.IntegrityError as except_msg:
            flash(gettext("User Error: %(err)s", err=except_msg), "error")
    else:
        flash_form_errors(formAddUser)


def user_mod(formModUser):
    if deny_guest_user():
        return redirect('/')

    try:
        if formModUser.validate():

            with session_scope(current_app.config['USER_DB_PATH']) as db_session:
                mod_user = db_session.query(Users).filter(
                    Users.user_name == formModUser.modUsername.data).first()
                mod_user.user_email = formModUser.modEmail.data
                # Only change the password if it's entered in the form
                logout_user = False
                if formModUser.modPassword.data is not '':
                    if (test_password(formModUser.modPassword.data) and
                            formModUser.modPassword.data == formModUser.modPassword_repeat.data):
                        mod_user.user_password_hash = bcrypt.hashpw(formModUser.modPassword.data.encode('utf-8'), bcrypt.gensalt())
                        if session['user_name'] == formModUser.modUsername.data:
                            logout_user = True
                mod_user.user_restriction = formModUser.modGroup.data
                mod_user.user_theme = formModUser.modTheme.data
                if session['user_name'] == formModUser.modUsername.data:
                    session['user_theme'] = formModUser.modTheme.data
                db_session.commit()
                if logout_user:
                    return 'logout'
        else:
            flash_form_errors(formModUser)
    except Exception as except_msg:
        flash(gettext("Settings Error: %(err)s", err=except_msg), "error")


def user_del(formDelUser):
    if deny_guest_user():
        return redirect('/')

    if formDelUser.validate():
        delete_user(current_app.config['USER_DB_PATH'], Users, formDelUser.delUsername.data)
        if formDelUser.delUsername.data == session['user_name']:
            return 'logout'
    else:
        flash_form_errors(formDelUser)


#
# Settings modifications
#

#
# General
#

def settings_general_mod(formModGeneral):
    if deny_guest_user():
        return redirect('/settings')

    try:
        if formModGeneral.validate():
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_misc = db_session.query(Misc).one()
                force_https = mod_misc.force_https
                mod_misc.language = formModGeneral.language.data
                mod_misc.force_https = formModGeneral.forceHTTPS.data
                mod_misc.hide_alert_success = formModGeneral.hideAlertSuccess.data
                mod_misc.hide_alert_info = formModGeneral.hideAlertInfo.data
                mod_misc.relay_stats_volts = formModGeneral.relayStatsVolts.data
                mod_misc.relay_stats_cost = formModGeneral.relayStatsCost.data
                mod_misc.relay_stats_currency = formModGeneral.relayStatsCurrency.data
                mod_misc.relay_stats_dayofmonth = formModGeneral.relayStatsDayOfMonth.data
                mod_misc.hide_alert_warning = formModGeneral.hideAlertWarning.data
                mod_misc.stats_opt_out = formModGeneral.stats_opt_out.data
                db_session.commit()

                if force_https != formModGeneral.forceHTTPS.data:
                    # Force HTTPS option changed.
                    # Reload web server with new settings.
                    wsgi_file = INSTALL_DIRECTORY+'/mycodo_flask.wsgi'
                    with open(wsgi_file, 'a'):
                        os.utime(wsgi_file, None)
        else:
            flash_form_errors(formModGeneral)
    except Exception as except_msg:
        flash(gettext("Settings Error: %(err)s", err=except_msg), "error")


#
# Camera
#

def settings_camera_mod(formModCamera):
    if deny_guest_user():
        return redirect('/settings')

    try:
        if formModCamera.validate():
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_camera = db_session.query(CameraStill).one()
                mod_camera.hflip = formModCamera.hflip.data
                mod_camera.vflip = formModCamera.vflip.data
                mod_camera.rotation = formModCamera.rotation.data
                db_session.commit()
        else:
            flash_form_errors(formModCamera)
    except Exception as except_msg:
        flash(gettext("Settings Error: %(err)s", err=except_msg), "error")


#
# Alerts
#

def settings_alert_mod(formModAlert):
    if deny_guest_user():
        return redirect('/alert')

    try:
        if formModAlert.validate():
            with session_scope(current_app.config['MYCODO_DB_PATH']) as db_session:
                mod_smtp = db_session.query(SMTP).one()
                if formModAlert.sendTestEmail.data:
                    send_email(False, mod_smtp.host,
                               mod_smtp.ssl, mod_smtp.port,
                               mod_smtp.user, mod_smtp.passw,
                               mod_smtp.email_from, formModAlert.testEmailTo.data,
                               "This is a test email from Mycodo")
                    flash(gettext("Test email sent to %(recip)s. Check your "
                                  "inbox to see if it was successful.",
                                  recip=formModAlert.testEmailTo.data),
                          "success")
                else:
                    mod_smtp.host = formModAlert.smtpHost.data
                    mod_smtp.port = formModAlert.smtpPort.data
                    mod_smtp.ssl = formModAlert.sslEnable.data
                    mod_smtp.user = formModAlert.smtpUser.data
                    if formModAlert.smtpPassword.data:
                        mod_smtp.passw = formModAlert.smtpPassword.data
                    mod_smtp.email_from = formModAlert.smtpFromEmail.data
                    mod_smtp.hourly_max = formModAlert.smtpMaxPerHour.data
                    db_session.commit()
        else:
            flash_form_errors(formModAlert)
    except Exception as except_msg:
        flash(gettext("Settings Error: %(err)s", err=except_msg), "error")


#
# Miscellaneous
#

def daemonActive():
    """Check for daemon lock file and return True if exists"""
    if os.path.isfile(DAEMON_PID_FILE):
        return True
    return False


def reorderList(modified_list, item, direction):
    """Reorder entry in a comma-separated list either up or down"""
    from_position = modified_list.index(item)
    if direction == "up":
        if from_position == 0:
            return 'error', 'Cannot move above the first item in the list'
        to_position = from_position - 1
    elif direction == 'down':
        if from_position == len(modified_list) - 1:
            return 'error', 'Cannot move below the last item in the list'
        to_position = from_position + 1
    else:
        return 'error', []
    modified_list.insert(to_position, modified_list.pop(from_position))
    return 'success', modified_list


def db_retrieve_table(database, table, first=False, device_id=''):
    """Return table data from database SQL query"""
    with session_scope(database) as new_session:
        if first:
            return_table = new_session.query(table).first()
        elif device_id:
            return_table = new_session.query(table).filter(table.id == device_id).first()
        else:
            return_table = new_session.query(table).all()
        new_session.expunge_all()
        new_session.close()
    return return_table


def delete_user(db_path, users, username):
    """Delete user from SQL database"""
    try:
        with session_scope(db_path) as db_session:
            user = db_session.query(users).filter(
                users.user_name == username).first()
            db_session.delete(user)
            flash(gettext("User %(user)s deleted", user=username), "success")
            return 1
    except sqlalchemy.orm.exc.NoResultFound:
        flash(gettext("User %(user)s not found", user=username), "success")
        return 0


def delete_entry_with_id(db_path, table, entry_id):
    """Delete SQL database entry with specific id"""
    try:
        with session_scope(db_path) as db_session:
            entries = db_session.query(table).filter(
                table.id == entry_id).first()
            db_session.delete(entries)
            flash(gettext("Entry with ID %(id)s deleted", id=entry_id),
                  "success")
            return 1
    except sqlalchemy.orm.exc.NoResultFound:
        flash(gettext("Entry with ID %(id)s not found", id=entry_id), "error")
        return 0


def flash_form_errors(form):
    """Flashes form errors for easier display"""
    for field, errors in form.errors.items():
        for error in errors:
            flash(u"Error in the {} field - {}".format(
                getattr(form, field).label.text,
                error
                ), "error")


def deny_guest_user():
    if session['user_group'] == 'guest':
        flash(gettext("Guests are not permitted to do that"), "error")
        return True


def gzipped(f):
    """
    Allows gzipping the response of any view.
    Just add '@gzipped' after the '@app'.
    Used mainly for sending large amounts of data for graphs.
    """
    @functools.wraps(f)
    def view_func(*args, **kwargs):
        @after_this_request
        def zipper(response):
            accept_encoding = request.headers.get('Accept-Encoding', '')

            if 'gzip' not in accept_encoding.lower():
                return response

            response.direct_passthrough = False

            if (response.status_code < 200 or
                response.status_code >= 300 or
                'Content-Encoding' in response.headers):
                return response
            gzip_buffer = IO()
            gzip_file = gzip.GzipFile(mode='wb',
                                      fileobj=gzip_buffer)

            gzip_file.write(response.data)
            gzip_file.close()

            response.data = gzip_buffer.getvalue()
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Vary'] = 'Accept-Encoding'
            response.headers['Content-Length'] = len(response.data)

            return response

        return f(*args, **kwargs)

    return view_func

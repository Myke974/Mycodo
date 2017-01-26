# coding=utf-8
""" collection of Method endpoints """
import logging
import datetime
import random
import string
import time

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for
)
from flask_babel import gettext

# Classes
from mycodo.databases.mycodo_db.models import (
    Method,
    Relay
)

# Functions
from mycodo import flaskforms
from mycodo import flaskutils
from mycodo.databases.utils import session_scope
from mycodo.mycodo_flask.general_routes import (
    before_blueprint_request,
    inject_mycodo_version,
    logged_in
)
from mycodo.utils.database import db_retrieve_table
from mycodo.utils.system_pi import get_sec
from mycodo.utils.method import (
    sine_wave_y_out,
    bezier_curve_y_out
)

logger = logging.getLogger('mycodo.mycodo_flask.methods')

blueprint = Blueprint('method_routes',
                      __name__,
                      static_folder='../static',
                      template_folder='../templates')
blueprint.before_request(before_blueprint_request)  # check if admin was created


@blueprint.context_processor
def inject_dictionary():
    return inject_mycodo_version()


@blueprint.route('/method-data/<method_type>/<method_id>')
def method_data(method_type, method_id):
    """
    Returns options for a particular method
    This includes sets of (time, setpoint) data.
    """
    logger.debug('called method_data(method_type={type}, '
                 'method_id={id})'.format(type=method_type, id=method_id))
    if not logged_in():
        return redirect(url_for('general_routes.home'))

    method = db_retrieve_table(
        current_app.config['MYCODO_DB_PATH'], Method)

    # First method column with general information about method
    method_key = method.filter(Method.method_id == method_id)
    method_key = method_key.filter(Method.method_order == 0).first()

    # User-edited lines of each method
    method = method.filter(Method.method_id == method_id)
    method = method.filter(Method.method_order > 0)
    method = method.filter(Method.relay_id == None)
    method = method.order_by(Method.method_order.asc()).all()

    method_list = []
    if method_key.method_type == "Date":
        for each_method in method:
            if each_method.end_setpoint == None:
                end_setpoint = each_method.start_setpoint
            else:
                end_setpoint = each_method.end_setpoint

            start_time = datetime.datetime.strptime(
                each_method.start_time, '%Y-%m-%d %H:%M:%S')
            end_time = datetime.datetime.strptime(
                each_method.end_time, '%Y-%m-%d %H:%M:%S')

            is_dst = time.daylight and time.localtime().tm_isdst > 0
            utc_offset_ms = (time.altzone if is_dst else time.timezone)
            method_list.append(
                [(int(start_time.strftime("%s")) - utc_offset_ms) * 1000,
                 each_method.start_setpoint])
            method_list.append(
                [(int(end_time.strftime("%s")) - utc_offset_ms) * 1000,
                 end_setpoint])
            method_list.append(
                [(int(start_time.strftime("%s")) - utc_offset_ms) * 1000,
                 None])

    elif method_key.method_type == "Daily":
        for each_method in method:
            if each_method.end_setpoint is None:
                end_setpoint = each_method.start_setpoint
            else:
                end_setpoint = each_method.end_setpoint
            method_list.append(
                [get_sec(each_method.start_time) * 1000,
                 each_method.start_setpoint])
            method_list.append(
                [get_sec(each_method.end_time) * 1000,
                 end_setpoint])
            method_list.append(
                [get_sec(each_method.start_time) * 1000,
                 None])

    elif method_key.method_type == "DailyBezier":
        points_x = 700
        seconds_in_day = 60 * 60 * 24
        P0 = (method_key.x0, method_key.y0)
        P1 = (method_key.x1, method_key.y1)
        P2 = (method_key.x2, method_key.y2)
        P3 = (method_key.x3, method_key.y3)
        for n in range(points_x):
            percent = n / float(points_x)
            second_of_day = percent * seconds_in_day
            y = bezier_curve_y_out(method_key.shift_angle,
                                   P0, P1, P2, P3,
                                   second_of_day)
            method_list.append([percent * seconds_in_day * 1000, y])

    elif method_key.method_type == "DailySine":
        points_x = 700
        seconds_in_day = 60 * 60 * 24
        for n in range(points_x):
            percent = n / float(points_x)
            angle = n / float(points_x) * 360
            y = sine_wave_y_out(method_key.amplitude, method_key.frequency,
                                method_key.shift_angle, method_key.shift_y,
                                angle)
            method_list.append([percent * seconds_in_day * 1000, y])

    elif method_key.method_type == "Duration":
        first_entry = True
        start_duration = 0
        end_duration = 0
        for each_method in method:
            if each_method.end_setpoint is None:
                end_setpoint = each_method.start_setpoint
            else:
                end_setpoint = each_method.end_setpoint
            if first_entry:
                method_list.append([0, each_method.start_setpoint])
                method_list.append([each_method.duration_sec, end_setpoint])
                start_duration += each_method.duration_sec
                first_entry = False
            else:
                end_duration = start_duration + each_method.duration_sec

                method_list.append(
                    [start_duration, each_method.start_setpoint])
                method_list.append(
                    [end_duration, end_setpoint])

                start_duration += each_method.duration_sec

    return jsonify(method_list)


@blueprint.route('/method', methods=('GET', 'POST'))
def method_list():
    """ List all methods on one page with a graph for each """
    if not logged_in():
        return redirect(url_for('general_routes.home'))

    form_create_method = flaskforms.CreateMethod()

    # TODO: Move to Flask-SQLAlchemy. This creates errors in the HTTP log: "SQLite objects created in a thread can only be used in that same thread"
    method = db_retrieve_table(
        current_app.config['MYCODO_DB_PATH'], Method)

    method_all = method.filter(Method.method_order > 0)
    method_all = method_all.filter(Method.relay_id == None).all()
    method = method.filter(Method.method_order == 0).all()

    return render_template('pages/method-list.html',
                           method=method,
                           method_all=method_all,
                           form_create_method=form_create_method)


@blueprint.route('/method-build/<method_type>/<method_id>', methods=('GET', 'POST'))
def method_builder(method_type, method_id):
    """
    Page to edit the details of each method
    This includes the (time, setpoint) data sets
    """
    logger.debug('called method_builder(method_type={type}, '
                 'method_id={id})'.format(type=method_type, id=method_id))
    if not logged_in():
        return redirect(url_for('general_routes.home'))

    # Used in software tests to verify function is executing as admin
    if method_type == '1':
        return 'admin logged in'

    if method_type in ['Date', 'Duration', 'Daily',
                       'DailySine', 'DailyBezier', '0']:
        form_create_method = flaskforms.CreateMethod()
        form_add_method = flaskforms.AddMethod()
        form_mod_method = flaskforms.ModMethod()

        # Create new method
        if method_type == '0':
            random_id = ''.join([random.choice(
                string.ascii_letters + string.digits) for _ in xrange(8)])
            method_id = random_id
            method_type = form_create_method.method_type.data
            form_fail = flaskutils.method_create(form_create_method,
                                                 method_id)
            if not form_fail:
                flash(gettext("New Method successfully created. You may now "
                              "add time points"), "success")
                return redirect('/method-build/{}/{}'.format(
                    method_type, method_id))
            else:
                flash(gettext("Could not create method"), "error")

        method = db_retrieve_table(
            current_app.config['MYCODO_DB_PATH'], Method)

        # The single table entry that holds the method type information
        method_key = method.filter(Method.method_id == method_id)
        method_key = method_key.filter(Method.method_order == 0).first()

        # The table entries with time, setpoint, and relay data, sorted by order
        method_list = method.filter(Method.method_order > 0)
        method_list = method_list.order_by(Method.method_order.asc()).all()

        last_end_time = ''
        last_setpoint = ''
        if method_type in ['Date', 'Daily']:
            last_method = method.filter(Method.method_id == method_key.method_id)
            last_method = last_method.filter(Method.method_order > 0)
            last_method = last_method.filter(Method.relay_id == None)
            last_method = last_method.order_by(Method.method_order.desc()).first()

            # Get last entry end time and setpoint to populate the form
            if last_method is None:
                last_end_time = ''
                last_setpoint = ''
            else:
                last_end_time = last_method.end_time
                if last_method.end_setpoint is not None:
                    last_setpoint = last_method.end_setpoint
                else:
                    last_setpoint = last_method.start_setpoint

        # method = db_retrieve_table(
        #     current_app.config['MYCODO_DB_PATH'], Method)
        relay = db_retrieve_table(
            current_app.config['MYCODO_DB_PATH'], Relay, entry='all')

        if request.method == 'POST':
            form_name = request.form['form-name']
            if form_name == 'addMethod':
                form_fail = flaskutils.method_add(form_add_method, method)
            elif form_name in ['modMethod', 'renameMethod']:
                form_fail = flaskutils.method_mod(form_mod_method, method)
            if (form_name in ['addMethod', 'modMethod', 'renameMethod'] and
                    not form_fail):
                return redirect('/method-build/{}/{}'.format(
                    method_type, method_id))

        return render_template('pages/method-build.html',
                               method=method,
                               relay=relay,
                               method_key=method_key,
                               method_list=method_list,
                               method_id=method_id,
                               method_type=method_type,
                               last_end_time=last_end_time,
                               last_setpoint=last_setpoint,
                               form_create_method=form_create_method,
                               form_add_method=form_add_method,
                               form_mod_method=form_mod_method)

    return redirect('/method')


@blueprint.route('/method-delete/<method_id>')
def method_delete(method_id):
    """Delete a method"""
    if not logged_in():
        return redirect(url_for('general_routes.home'))

    try:
        with session_scope(current_app.config['MYCODO_DB_PATH']) as new_session:
            method = new_session.query(Method)
            method.filter(Method.method_id == method_id).delete()
    except Exception as except_msg:
        flash("Error while deleting Method: "
              "{}".format(except_msg), "error")
    return redirect(url_for('method_routes.method_list'))

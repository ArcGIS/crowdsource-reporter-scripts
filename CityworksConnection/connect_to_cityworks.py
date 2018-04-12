# ------------------------------------------------------------------------------
# Name:        connect_to_cityworks.py
# Purpose:     Pass reports from esri to cityworks
#
# Author:      alli6394
#
# Created:     31/10/2016
#
# Version: Unreleased

# Copyright 2016 Esri

#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

# ------------------------------------------------------------------------------

from arcgis.gis import GIS  # , Group, Layer
from arcgis.features import FeatureLayer  # , Table

import requests
import json
from os import path, remove
from datetime import datetime
from dateutil.tz import gettz
from dateutil.parser import parse

cw_token = ""
baseUrl = ""
log_to_file = True


def get_response(url, params):
    response = requests.get(url, params=params)
    return json.loads(response.text)


def get_cw_token(user, pwd, isCWOL):
    """Retrieve a token for Cityworks access"""
    if isCWOL:
        data = {"LoginName": user, "Password": pwd}
        json_data = json.dumps(data, separators=(",", ":"))
        params = {"data": json_data}
        url = "https://login.cityworksonline.com/Services/authentication/CityworksOnlineAuthenticate"

        response = get_response(url, params)

        if response["Status"] is not 0:
            return "error: {}: {}".format(response["Status"],
                                          response["Message"])
        else:
            pwd = response["Value"]["Token"]
    data = {"LoginName": user, "Password": pwd}
    json_data = json.dumps(data, separators=(",", ":"))
    params = {"data": json_data}
    url = "{}/Services/authentication/authenticate".format(baseUrl)

    response = get_response(url, params)

    if response["Status"] is not 0:
        return "error: {}: {}".format(response["Status"],
                                      response["Message"])
    else:
        global cw_token
        cw_token = response["Value"]["Token"]

        return "success"


def get_wkid():
    """Retrieve the WKID of the cityworks layers"""

    params = {"token": cw_token}
    url = "{}/Services/AMS/Preferences/User".format(baseUrl)

    response = get_response(url, params)

    try:
        return response["Value"]["SpatialReference"]

    except KeyError:
        return "error"


def get_problem_types():
    """Retrieve a dict of problem types from cityworks"""

    data = {"ForPublicOnly": "true"}
    json_data = json.dumps(data, separators=(",", ":"))
    params = {"data": json_data, "token": cw_token}
    url = "{}/Services/AMS/ServiceRequest/Problems".format(baseUrl)

    try:
        response = get_response(url, params)

        values = {}
        for val in response["Value"]:
            values[val["ProblemCode"].upper()] = int(val["ProblemSid"])

        return values

    except Exception as error:
        return "error: " + str(error)


def submit_to_cw(row, prob_types, fields, oid, typefields):

    attrs = row.attributes
    geometry = row.geometry

    try:
        prob_sid = prob_types[attrs[typefields[1]].upper()]

    except KeyError:
        if attrs[typefields[1]].strip() == "":
            msg = "WARNING: No problem type provided. Record {} not exported.".format(oid)
            return msg
        else:
            ptype = attrs[typefields[1]]
            msg = "WARNING: Problem type {} not found in Cityworks. Record {} not exported.".format(ptype, oid)
            return msg

    except AttributeError:
        msg = "WARNING: Record {} not exported due to missing value in field {}".format(oid, typefields[1])
        return msg

    # Build dictionary of values to submit to CW
    values = {}
    for fieldset in fields:
        c_field, a_field = fieldset
        values[c_field] = str(attrs[a_field])
    values["X"] = geometry["x"]
    values["Y"] = geometry["y"]
    values[typefields[0]] = prob_sid
    values["InitiatedByApp"] = "Crowdsource Reporter"

    # Convert dict to pretty print json
    json_data = json.dumps(values, separators=(",", ":"))
    params = {"data": json_data, "token": cw_token}

    # Submit report to Cityworks.
    url = "{}/Services/AMS/ServiceRequest/Create".format(baseUrl)
    response = get_response(url, params)

    try:
        return response["Value"]

    except TypeError:
        try:
            return 'error: {}'.format(response['ErrorMessages'])
        except KeyError:
            return 'error: {}'.format(response['Message'])


def copy_attachment(attachmentmgr, attachment, oid, requestid):

    # download attachment
    attpath = attachmentmgr.download(oid, attachment["id"])

    # upload attachment
    file = open(attpath, "rb")
    data = {"RequestId": requestid}
    json_data = json.dumps(data, separators=(",", ":"))
    params = {"token": cw_token, "data": json_data}
    files = {"file": (attachment["name"], file)}
    url = "{}/Services/AMS/Attachments/AddRequestAttachment".format(baseUrl)
    response = requests.post(url, files=files, data=params)

    # delete downloaded file
    file.close()
    remove(attpath)

    return json.loads(response.text)


def copy_comments(record, parent, fields, ids):

    values = {ids[0]: parent.attributes[ids[1]]}
    for field in fields:
        values[field[0]] = record.attributes[field[1]]

    json_data = json.dumps(values, separators=(",", ":"))
    params = {"data": json_data, "token": cw_token}
    url = "{}/Services/AMS/CustomerCall/AddToRequest".format(baseUrl)
    try:
        response = get_response(url, params)
        return response

    except json.decoder.JSONDecodeError:
        return 'error'


def get_parent(lyr, pkey_fld, record, fkey_fld):

    sql = "{} = '{}'".format(pkey_fld, record.attributes[fkey_fld])
    parents = lyr.query(where=sql)
    return parents.features[0]


def main(event, context):

    # Cityworks settings
    global baseUrl
    baseUrl = event["cityworks"]["url"]
    cwUser = event["cityworks"]["username"]
    cwPwd = event["cityworks"]["password"]
    timezone = event["cityworks"].get("timezone", "")
    isCWOL = event["cityworks"].get("isCWOL", False)

    # ArcGIS Online/Portal settings
    orgUrl = event["arcgis"]["url"]
    username = event["arcgis"]["username"]
    password = event["arcgis"]["password"]
    layers = event["arcgis"]["layers"]
    tables = event["arcgis"]["tables"]
    layerfields = event["fields"]["layers"]
    tablefields = event["fields"]["tables"]
    fc_flag = event["flag"]["field"]
    flag_values = [event["flag"]["on"], event["flag"]["off"]]
    ids = event["fields"]["ids"]
    probtypes = event["fields"]["type"]
    opendate = event["fields"].get("opendate", "")

    if log_to_file:
        from datetime import datetime as dt
        id_log = path.join(sys.path[0], "cityworks_log.log")
        log = open(id_log, "a")
        log.write("\n{} ".format(dt.now()))
        log.write("Sending reports to: {}\n".format(baseUrl))
    else:
        print("Sending reports to: {}".format(baseUrl))

    try:
        # Connect to org/portal
        gis = GIS(orgUrl, username, password)

        # Get token for CW
        status = get_cw_token(cwUser, cwPwd, isCWOL)

        if "error" in status:
            if log_to_file:
                log.write("Failed to get Cityworks token. {}\n".format(status))
            else:
                print("Failed to get Cityworks token. {}".format(status))
            raise Exception("Failed to get Cityworks token.  {}".format(status))

        # get wkid
        sr = get_wkid()

        if sr == "error":
            if log_to_file:
                log.write("Spatial reference not defined\n")
            else:
                print("Spatial reference not defined")
            raise Exception("Spatial reference not defined")

        # get problem types
        prob_types = get_problem_types()

        if prob_types == "error":
            if log_to_file:
                log.write("Problem types not defined\n")
            else:
                print("Problem types not defined")
            raise Exception("Problem types not defined")

        for layer in layers:
            lyr = FeatureLayer(layer, gis=gis)
            oid_fld = lyr.properties.objectIdField
            lyrname = lyr.properties["name"]

            # Get related table URL
            reltable = ""
            for relate in lyr.properties.relationships:
                url_pieces = layer.split("/")
                url_pieces[-1] = str(relate["relatedTableId"])
                table_url = "/".join(url_pieces)

                if table_url in tables:
                    reltable = table_url
                    break

            # query reports
            sql = "{}='{}'".format(fc_flag, flag_values[0])
            rows = lyr.query(where=sql, out_sr=sr)
            updated_rows = []

            for row in rows.features:
                oid = row.attributes[oid_fld]

                # Submit feature to the Cityworks database
                request = submit_to_cw(row, prob_types, layerfields, oid, probtypes)
                reqid = request["RequestId"]
                initDate = int(parse(request[opendate[0]]).replace(tzinfo=gettz(timezone)).timestamp() * 1000) if opendate else ""

                try:
                    if "WARNING" in reqid:
                        msg = "Warning generated while copying record from layer {} to Cityworks: {}".format(lyrname,
                                                                                                               reqid)
                        if log_to_file:
                            log.write(msg+'\n')
                        else:
                            print(msg)
                        continue
                    elif 'error' in reqid:
                        msg = "Error generated while copying record from layer {} to Cityworks: {}".format(lyrname,
                                                                                                             reqid)
                        if log_to_file:
                            log.write(msg+'\n')
                        else:
                            print(msg)
                        continue
                    else:
                        pass  # requestID is str = ok
                except TypeError:
                    pass  # requestID is a number = ok

                # attachments
                try:
                    attachmentmgr = lyr.attachments
                    attachments = attachmentmgr.get_list(oid)

                    for attachment in attachments:
                        response = copy_attachment(attachmentmgr, attachment, oid, reqid)
                        if response["Status"] is not 0:
                            try:
                                error = response["ErrorMessages"]
                            except KeyError:
                                error = response["Message"]

                            msg = "Error copying attachment from feature {} in layer {}: {}".format(oid,
                                                                                                      lyrname,
                                                                                                      error)
                            if log_to_file:
                                log.write(msg+'\n')
                            else:
                                print(msg)
                except RuntimeError:
                    pass  # feature layer doesn't support attachments

                # update the record in the service so that it evaluates falsely against sql
                sql = "{}='{}'".format(oid_fld, oid)
                row_orig = lyr.query(where=sql).features[0]
                row_orig.attributes[fc_flag] = flag_values[1]
                if opendate:
                    row_orig.attributes[opendate[1]] = initDate
                try:
                    row_orig.attributes[ids[1]] = reqid
                except TypeError:
                    row_orig.attributes[ids[1]] = str(reqid)

                updated_rows.append(row_orig)

            # apply edits to updated features
            if updated_rows:
                status = lyr.edit_features(updates=updated_rows)
                if log_to_file:
                    log.write("Status of updates to {}: {}\n".format(lyr.properties["name"], status))
                else:
                    print("Status of updates to {}: {}".format(lyr.properties["name"], status))

            # related records
            rellyr = FeatureLayer(reltable, gis=gis)
            relname = rellyr.properties['name']

            pkey_fld = lyr.properties.relationships[0]["keyField"]
            fkey_fld = rellyr.properties.relationships[0]["keyField"]
            sql = "{}='{}'".format(fc_flag, flag_values[0])
            rel_records = rellyr.query(where=sql)
            updated_rows = []

            for record in rel_records:
                rel_oid = record.attributes[oid_fld]
                parent = get_parent(lyr, pkey_fld, record, fkey_fld)

                # Upload comment attachments
                try:
                    attachmentmgr = rellyr.attachments
                    attachments = attachmentmgr.get_list(rel_oid)
                    for attachment in attachments:
                        response = copy_attachment(attachmentmgr, attachment, rel_oid, parent.attributes[ids[1]])
                        if response["Status"] is not 0:
                            try:
                                error = response["ErrorMessages"]
                            except KeyError:
                                error = response["Message"]
                            msg = "Error copying attachment. Record {} in table {}: {}".format(rel_oid,
                                                                                                 relname,
                                                                                                 error)
                            if log_to_file:
                                log.write(msg+'\n')
                            else:
                                print(msg)
                except RuntimeError:
                    pass  # table doesn't support attachments

                # Process comments
                response = copy_comments(record, parent, tablefields, ids)

                if 'error' in response:
                    if log_to_file:
                        log.write('Error accessing comment table {}\n'.format(relname))
                    else:
                        print('Error accessing comment table {}'.format(relname))
                    break

                elif response["Status"] is not 0:
                    try:
                        error = response["ErrorMessages"]
                    except KeyError:
                        error = response["Message"]
                    msg = "Error copying record {} from {}: {}".format(rel_oid, relname, error)
                    if log_to_file:
                        log.write(msg+'\n')
                    else:
                        print(msg)
                    continue
                else:
                    record.attributes[fc_flag] = flag_values[1]
                    updated_rows.append(record)

            # apply edits to updated records
            if updated_rows:
                status = rellyr.edit_features(updates=updated_rows)
                if log_to_file:
                    log.write("Status of updates to {} comments: {}\n".format(relname, status))
                else:
                    print("Status of updates to {} comments: {}".format(relname, status))

            print("Finished processing: {}".format(lyrname))

    except Exception as ex:
        print("error: " + str(ex))

    finally:
        if log_to_file:
            log.close()


if __name__ == "__main__":

    import sys

    configfile = sys.argv[1]

    with open(configfile) as configreader:
        config = json.load(configreader)

    main(config, "context")
